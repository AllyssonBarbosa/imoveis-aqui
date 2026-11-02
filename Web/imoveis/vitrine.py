"""Regras da vitrine pública (W09): o que aparece e como cada imóvel é comparado.

Fica aqui, no Django, e não no app — o app só mostra o que a API devolve.
A vitrine não tem usuário logado, então não filtra pela empresa do usuário:
mostra imóveis de todas as empresas, mas só os publicados de empresa ativa.
"""

from django.db.models import Case, F, When

from .models import Imovel

APARTAMENTO = Imovel.Natureza.APARTAMENTO
CASA = Imovel.Natureza.CASA
TERRENO = Imovel.Natureza.TERRENO


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
