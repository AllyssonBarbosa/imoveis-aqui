"""Regras da vitrine pública (W09): o que aparece e como cada imóvel é comparado.

Fica aqui, no Django, e não no app — o app só mostra o que a API devolve.
A vitrine não tem usuário logado, então não filtra pela empresa do usuário:
mostra imóveis de todas as empresas, mas só publicado, com situação disponível, de empresa
ativa — regra única da W08 (ImovelQuerySet.na_vitrine), sem cópia aqui.
"""

import operator
import re
from decimal import Decimal, InvalidOperation
from functools import reduce

from django.db.models import Case, Count, F, Q, When
from django.db.models.functions import Coalesce

from localizacao.models import Cidade

from .models import Imovel

APARTAMENTO = Imovel.Natureza.APARTAMENTO
CASA = Imovel.Natureza.CASA
LOTE = Imovel.Natureza.LOTE
TERRENO = Imovel.Natureza.TERRENO
LOTE = Imovel.Natureza.LOTE

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
      privativa (apartamento), total (terreno) ou a do lote
    - o lote não tem endereço próprio: cidade e bairro vêm do endereço do Imóvel
      (Loteamento.cidade não conta para a vitrine)
    - quartos / suites / vagas: da casa ou do apartamento (terreno fica sem)
    """
    return (
        Imovel.objects.na_vitrine()
        .annotate(
            cidade_anuncio=_do_anuncio("cidade_id", "cidade_id"),
            bairro_anuncio=_do_anuncio("bairro", "bairro"),
            area_anuncio=Case(
                When(natureza=CASA, then=F("casa__area_construida")),
                When(natureza=APARTAMENTO, then=F("apartamento__area_privativa")),
                When(natureza=TERRENO, then=F("terreno__area_total")),
                When(natureza=LOTE, then=F("lote__area")),
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


# Formato aceito para número na URL: só dígitos ASCII (`[0-9]`, nunca `\d`, que aceita dígito de
# outros alfabetos), sinal opcional e, no decimal, fração com ponto. Fora disso (NaN, Infinity,
# expoente, `_`, espaço) é inválido — `Decimal()` e `int()` aceitariam tudo isso.
_FORMATO_DECIMAL = re.compile(r"-?[0-9]+(\.[0-9]+)?")
_FORMATO_INTEIRO = re.compile(r"-?[0-9]+")

# Maior preço que as colunas de preço guardam (max_digits=12, decimal_places=2).
MAXIMO_FILTRO_PRECO = Decimal("9999999999.99")
# Maior área que as colunas de área guardam (max_digits=10): `area_anuncio` é um Case sobre área
# construída, privativa, total e do lote. Filtrar além disso não muda o resultado, e um backend que
# ajuste o parâmetro ao (10,2) levantaria InvalidOperation (500). Por isso a área não usa o teto do preço.
MAXIMO_FILTRO_AREA = Decimal("99999999.99")
# Teto do PositiveSmallIntegerField (quartos, suítes e vagas).
MAXIMO_FILTRO_INTEIRO = 32767

# Tetos de texto: `busca` e CSV de `bairro` (WR-02). 100 é o max_length de `Endereco.bairro`.
MAXIMO_CARACTERES_BUSCA = 200
MAXIMO_BAIRROS = 20
MAXIMO_CARACTERES_BAIRRO = 100

_MENSAGEM_NUL = "O texto tem um caractere inválido."

# Id de característica na URL: só dígitos ASCII (mesmo critério do `_FORMATO_INTEIRO`; `int()` aceitaria
# `-1`, `1_0` e dígito de outros alfabetos), com no máximo 18 dígitos — cabe no bigint da chave primária.
# O teto vale também para zeros à esquerda: sem ele, 4301+ dígitos estourariam o limite do `int()` do
# Python (ValueError, 500) e um id de 30 dígitos passaria do range do inteiro ligado à consulta (WR-07).
_FORMATO_ID = re.compile(r"[0-9]{1,18}")
# Maior que o catálogo semeado e que qualquer combinação útil de "tem todas". Cada id custava um JOIN
# na tabela M2M (CR-01); o teto barra a requisição antes de qualquer consulta.
MAXIMO_CARACTERISTICAS = 20


def _numero(params, nome, maximo, tipo=Decimal):
    """Lê um filtro numérico da URL — valor inválido vira FiltroInvalido com mensagem clara.

    Ordem: vazio é ignorado; o texto cru precisa casar com o formato permitido (allowlist ASCII);
    converte; decimal precisa ser finito; depois negativo e teto (`maximo`, o da coluna filtrada).
    """
    valor = params.get(nome)
    if valor in (None, ""):
        return None
    formato = _FORMATO_DECIMAL if tipo is Decimal else _FORMATO_INTEIRO
    if not formato.fullmatch(valor):
        raise FiltroInvalido({nome: "Informe um número válido."})
    try:
        numero = tipo(valor)
    except (InvalidOperation, ValueError):
        raise FiltroInvalido({nome: "Informe um número válido."})
    if tipo is Decimal and not numero.is_finite():
        raise FiltroInvalido({nome: "Informe um número válido."})
    if numero < 0:
        raise FiltroInvalido({nome: "O valor não pode ser negativo."})
    if numero > maximo:
        raise FiltroInvalido({nome: "O valor passa do máximo permitido."})
    return numero


def _texto(params, nome, maximo):
    """Lê um texto livre da URL: sem byte NUL, aparado e com no máximo `maximo` caracteres."""
    valor = params.get(nome, "")
    # o Postgres não guarda NUL em texto: o driver levantaria DataError (500). Recusa em Python,
    # antes da consulta, para o erro valer em qualquer banco.
    if "\x00" in valor:
        raise FiltroInvalido({nome: _MENSAGEM_NUL})
    valor = valor.strip()
    if len(valor) > maximo:
        raise FiltroInvalido({nome: f"Use no máximo {maximo} caracteres."})
    return valor


def _bairros_do_filtro(params):
    """Bairros do `?bairro=Moema,Pinheiros`: itens aparados, vazios descartados, com tetos."""
    valor = params.get("bairro", "")
    if "\x00" in valor:
        raise FiltroInvalido({"bairro": _MENSAGEM_NUL})
    bairros = [item.strip() for item in valor.split(",") if item.strip()]
    if len(bairros) > MAXIMO_BAIRROS:
        raise FiltroInvalido({"bairro": f"Escolha no máximo {MAXIMO_BAIRROS} bairros."})
    if any(len(bairro) > MAXIMO_CARACTERES_BAIRRO for bairro in bairros):
        raise FiltroInvalido(
            {"bairro": f"Cada bairro tem no máximo {MAXIMO_CARACTERES_BAIRRO} caracteres."}
        )
    return bairros


def _ids_de_caracteristicas(params):
    """Ids do `?caracteristicas=1,3`: itens aparados, vazios descartados, repetidos contam uma vez.

    Cada item precisa ser inteiro ASCII positivo; passou de `MAXIMO_CARACTERISTICAS` ids distintos é inválido.
    """
    valor = params.get("caracteristicas", "").strip()
    if not valor:
        return set()
    ids = set()
    for item in valor.split(","):
        item = item.strip()
        if not item:
            continue
        if not _FORMATO_ID.fullmatch(item):
            raise FiltroInvalido({"caracteristicas": "Informe os ids separados por vírgula."})
        numero = int(item)  # seguro: até 18 dígitos ASCII
        if numero <= 0:
            raise FiltroInvalido({"caracteristicas": "Informe os ids separados por vírgula."})
        ids.add(numero)
    if len(ids) > MAXIMO_CARACTERISTICAS:
        raise FiltroInvalido(
            {"caracteristicas": f"Escolha no máximo {MAXIMO_CARACTERISTICAS} características."}
        )
    return ids


def _escolha(params, nome, opcoes):
    valor = params.get(nome)
    if valor in (None, ""):
        return None
    if valor not in opcoes:
        raise FiltroInvalido({nome: f"Opções válidas: {', '.join(opcoes)}."})
    return valor


def _escolhas(params, nome, opcoes):
    """Vários valores separados por vírgula (OU). Itens vazios e espaços são descartados;
    qualquer item fora de `opcoes` é inválido.
    """
    itens = [item.strip() for item in params.get(nome, "").split(",") if item.strip()]
    for item in itens:
        if item not in opcoes:
            raise FiltroInvalido({nome: f"Opções válidas: {', '.join(opcoes)}."})
    return itens


def buscar_na_vitrine(cidade, params):
    """Busca da vitrine dentro de uma cidade — usada pela API e pelo site.

    `params` são os parâmetros da URL (request.GET ou request.query_params).
    Filtro com valor inválido levanta FiltroInvalido (a API devolve 400; o site mostra a mensagem).
    """
    queryset = imoveis_da_vitrine().filter(cidade_anuncio=cidade.pk)

    busca = _texto(params, "busca", MAXIMO_CARACTERES_BUSCA)
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

    # ?natureza=CASA,LOTE — OU entre as naturezas; um valor só continua funcionando
    naturezas = _escolhas(params, "natureza", Imovel.Natureza.values)
    if naturezas:
        queryset = queryset.filter(natureza__in=naturezas)

    # o preço comparado depende da finalidade buscada; sem finalidade, vale o que houver
    if finalidade == Imovel.Finalidade.VENDA:
        preco = F("preco_venda")
    elif finalidade == Imovel.Finalidade.ALUGUEL:
        preco = F("preco_aluguel")
    else:
        preco = Coalesce("preco_venda", "preco_aluguel")
    queryset = queryset.annotate(preco_anuncio=preco)

    # cada faixa tem o teto da própria coluna: preço (12 dígitos) e área (10 dígitos) diferem
    faixas = {
        "preco_min": ("preco_anuncio__gte", MAXIMO_FILTRO_PRECO),
        "preco_max": ("preco_anuncio__lte", MAXIMO_FILTRO_PRECO),
        "area_min": ("area_anuncio__gte", MAXIMO_FILTRO_AREA),
        "area_max": ("area_anuncio__lte", MAXIMO_FILTRO_AREA),
    }
    for nome, (lookup, maximo) in faixas.items():
        valor = _numero(params, nome, maximo)
        if valor is not None:
            queryset = queryset.filter(**{lookup: valor})

    # quartos/suítes/vagas são "no mínimo" — quem pede 2 quartos aceita 3
    minimos = {"quartos": "quartos_anuncio__gte", "suites": "suites_anuncio__gte", "vagas": "vagas_anuncio__gte"}
    for nome, lookup in minimos.items():
        valor = _numero(params, nome, MAXIMO_FILTRO_INTEIRO, tipo=int)
        if valor is not None:
            queryset = queryset.filter(**{lookup: valor})

    # ?bairro=Moema,Pinheiros — OU entre os bairros, sem diferenciar maiúsculas (por isso o
    # OR de iexact e não __in). Bairro com vírgula no nome não cabe no CSV (risco aceito, A-05).
    bairros = _bairros_do_filtro(params)
    if bairros:
        queryset = queryset.filter(
            reduce(operator.or_, (Q(bairro_anuncio__iexact=bairro) for bairro in bairros))
        )

    # ?caracteristicas=1,3 — o imóvel precisa ter todas as escolhidas: uma só subconsulta conta
    # quantas delas ele tem (GROUP BY imóvel HAVING COUNT DISTINCT = quantidade pedida), sem um
    # JOIN por id (CR-01)
    ids = _ids_de_caracteristicas(params)
    if ids:
        com_todas = (
            Imovel.caracteristicas.through.objects.filter(caracteristica_id__in=ids)
            .values("imovel_id")
            .annotate(total=Count("caracteristica_id", distinct=True))
            .filter(total=len(ids))
            .values("imovel_id")
        )
        queryset = queryset.filter(pk__in=com_todas)

    ordenar = _escolha(params, "ordenar", list(ORDENACOES)) or "recentes"
    # o id no fim deixa a ordem estável entre as páginas quando há empate
    return (
        queryset.order_by(*ORDENACOES[ordenar], "-id")
        .distinct()
        .prefetch_related("fotos")
    )
