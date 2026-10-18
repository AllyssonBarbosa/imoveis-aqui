"""Regras da vitrine pública (W09): o que aparece e como cada imóvel é comparado.

Fica aqui, no Django, e não no app — o app só mostra o que a API devolve.
A vitrine não tem usuário logado, então não filtra pela empresa do usuário:
mostra imóveis de todas as empresas, mas só os publicados de empresa ativa.
"""

from decimal import Decimal, InvalidOperation

from django.db.models import Case, F, Q, When
from django.db.models.functions import Coalesce

from localizacao.models import Cidade

from .models import Imovel

APARTAMENTO = Imovel.Natureza.APARTAMENTO
CASA = Imovel.Natureza.CASA
TERRENO = Imovel.Natureza.TERRENO

IMOVEIS_POR_PAGINA = 20

ORDENACOES = {
    "recentes": [F("criado_em").desc()],
    "preco": [F("preco_anuncio").asc(nulls_last=True)],
    "-preco": [F("preco_anuncio").desc(nulls_last=True)],
    "area": [F("area_anuncio").asc(nulls_last=True)],
    "-area": [F("area_anuncio").desc(nulls_last=True)],
}


class FiltroInvalido(Exception):
    """Filtro da busca com valor inválido. `erros` é {nome_do_filtro: mensagem}.

    A API transforma em 400; o site mostra a mensagem acima da vitrine.
    """

    def __init__(self, erros):
        super().__init__(erros)
        self.erros = erros


def _do_anuncio(campo_do_imovel, campo_do_predio):
    """Apartamento usa o endereço do prédio (ver Imovel.endereco_do_anuncio)."""
    return Case(
        When(natureza=APARTAMENTO, then=F(f"apartamento__predio__endereco__{campo_do_predio}")),
        default=F(f"endereco__{campo_do_imovel}"),
    )


def imoveis_da_vitrine():
    """Imóveis visíveis ao público, já com os campos usados nos filtros e na ordenação.

    Anotações (colunas calculadas na consulta):
    - cidade_anuncio / bairro_anuncio: do endereço do anúncio (prédio, se apartamento)
    - area_anuncio: a área principal de cada natureza — construída (casa),
      privativa (apartamento) ou total (terreno)
    - quartos / suites / vagas: da casa ou do apartamento (terreno fica sem)
    """
    return (
        Imovel.objects.filter(publicado=True, empresa__ativa=True)
        .annotate(
            cidade_anuncio=_do_anuncio("cidade_id", "cidade_id"),
            bairro_anuncio=_do_anuncio("bairro", "bairro"),
            area_anuncio=Case(
                When(natureza=CASA, then=F("casa__area_construida")),
                When(natureza=APARTAMENTO, then=F("apartamento__area_privativa")),
                When(natureza=TERRENO, then=F("terreno__area_total")),
            ),
            quartos_anuncio=Case(
                When(natureza=CASA, then=F("casa__quartos")),
                When(natureza=APARTAMENTO, then=F("apartamento__quartos")),
            ),
            suites_anuncio=Case(
                When(natureza=CASA, then=F("casa__suites")),
                When(natureza=APARTAMENTO, then=F("apartamento__suites")),
            ),
            vagas_anuncio=Case(
                When(natureza=CASA, then=F("casa__vagas")),
                When(natureza=APARTAMENTO, then=F("apartamento__vagas")),
            ),
        )
    )


def cidades_atendidas():
    """Só as cidades que têm imóvel na vitrine — o visitante nunca escolhe uma cidade vazia."""
    return Cidade.objects.filter(pk__in=imoveis_da_vitrine().values("cidade_anuncio"))


def _numero(params, nome, tipo=Decimal):
    """Lê um filtro numérico da URL — valor inválido vira FiltroInvalido com mensagem clara."""
    valor = params.get(nome)
    if valor in (None, ""):
        return None
    try:
        numero = tipo(valor)
    except (InvalidOperation, ValueError):
        raise FiltroInvalido({nome: "Informe um número válido."})
    if numero < 0:
        raise FiltroInvalido({nome: "O valor não pode ser negativo."})
    return numero


def _escolha(params, nome, opcoes):
    valor = params.get(nome)
    if valor in (None, ""):
        return None
    if valor not in opcoes:
        raise FiltroInvalido({nome: f"Opções válidas: {', '.join(opcoes)}."})
    return valor


def buscar_na_vitrine(cidade, params):
    """Busca da vitrine dentro de uma cidade — usada pela API e pelo site.

    `params` são os parâmetros da URL (request.GET ou request.query_params).
    Filtro com valor inválido levanta FiltroInvalido.
    """
    queryset = imoveis_da_vitrine().filter(cidade_anuncio=cidade.pk)

    busca = params.get("busca", "").strip()
    if busca:
        queryset = queryset.filter(
            Q(titulo__icontains=busca)
            | Q(bairro_anuncio__icontains=busca)
            | Q(descricao__icontains=busca)
        )

    # "venda" também traz quem é "venda e aluguel" (e o mesmo pro aluguel)
    finalidade = _escolha(params, "finalidade", Imovel.Finalidade.values)
    if finalidade:
        queryset = queryset.filter(
            finalidade__in=[finalidade, Imovel.Finalidade.VENDA_E_ALUGUEL]
        )

    natureza = _escolha(params, "natureza", Imovel.Natureza.values)
    if natureza:
        queryset = queryset.filter(natureza=natureza)

    # o preço comparado depende da finalidade buscada; sem finalidade, vale o que houver
    if finalidade == Imovel.Finalidade.VENDA:
        preco = F("preco_venda")
    elif finalidade == Imovel.Finalidade.ALUGUEL:
        preco = F("preco_aluguel")
    else:
        preco = Coalesce("preco_venda", "preco_aluguel")
    queryset = queryset.annotate(preco_anuncio=preco)

    faixas = {
        "preco_min": "preco_anuncio__gte",
        "preco_max": "preco_anuncio__lte",
        "area_min": "area_anuncio__gte",
        "area_max": "area_anuncio__lte",
    }
    for nome, lookup in faixas.items():
        valor = _numero(params, nome)
        if valor is not None:
            queryset = queryset.filter(**{lookup: valor})

    # quartos/suítes/vagas são "no mínimo" — quem pede 2 quartos aceita 3
    minimos = {"quartos": "quartos_anuncio__gte", "suites": "suites_anuncio__gte", "vagas": "vagas_anuncio__gte"}
    for nome, lookup in minimos.items():
        valor = _numero(params, nome, tipo=int)
        if valor is not None:
            queryset = queryset.filter(**{lookup: valor})

    bairro = params.get("bairro", "").strip()
    if bairro:
        queryset = queryset.filter(bairro_anuncio__iexact=bairro)

    # ?caracteristicas=1,3 — o imóvel precisa ter todas as escolhidas
    caracteristicas = params.get("caracteristicas", "").strip()
    if caracteristicas:
        try:
            ids = {int(item) for item in caracteristicas.split(",") if item.strip()}
        except ValueError:
            raise FiltroInvalido({"caracteristicas": "Informe os ids separados por vírgula."})
        for caracteristica_id in ids:
            queryset = queryset.filter(caracteristicas=caracteristica_id)

    ordenar = _escolha(params, "ordenar", list(ORDENACOES)) or "recentes"
    # o id no fim deixa a ordem estável entre as páginas quando há empate
    return (
        queryset.order_by(*ORDENACOES[ordenar], "-id")
        .distinct()
        .prefetch_related("fotos")
    )
