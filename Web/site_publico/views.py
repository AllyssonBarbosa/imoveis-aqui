"""Site público (W10): a vitrine pelo navegador, sem login e sem cadastro.

Nenhuma regra mora aqui. O que é público, os filtros e a ordenação vêm de
imoveis/vitrine.py (os mesmos da API), e os dados chegam aos templates já
passados pelos serializers públicos da W09 — assim o template nem recebe
proprietário, código interno ou documento, e o site mostra exatamente o
que o app mostra.

Contato (W11): os botões da página do imóvel não levam o link pronto. Eles
enviam um POST para /imovel/<id>/contato/, que grava o contato e só então
redireciona para o WhatsApp, o telefone ou o e-mail (ver contatos/servicos.py).
"""

from django.core.paginator import Paginator
from django.db.models import Prefetch
from django.http import HttpResponseBadRequest, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from contas.api.serializers import CorretorPublicoSerializer
from contas.models import Usuario
from contatos.servicos import CanalIndisponivel, canais_disponiveis, link_whatsapp, registrar_contato
from empresas.api.serializers import EmpresaPublicaSerializer
from empresas.models import Empresa
from imoveis.api.serializers import ImovelVitrineDetalheSerializer, ImovelVitrineSerializer
from imoveis.models import Caracteristica, FotoImovel, Imovel
from imoveis.vitrine import (
    IMOVEIS_POR_PAGINA, FiltroInvalido, buscar_na_vitrine, cidades_atendidas, imoveis_da_empresa,
    imoveis_da_vitrine, imoveis_do_corretor,
)
from localizacao.models import Cidade

# a cidade escolhida fica na sessão (um cookie) — funciona sem login
CHAVE_CIDADE = "cidade_id"

# rótulos das opções de ordenar — as chaves são as mesmas de vitrine.ORDENACOES
ROTULOS_ORDENACAO = {
    "recentes": "Mais recentes",
    "preco": "Menor preço",
    "-preco": "Maior preço",
    "area": "Menor área",
    "-area": "Maior área",
}


def _cidade_atual(request):
    cidade_id = request.session.get(CHAVE_CIDADE)
    return Cidade.objects.filter(pk=cidade_id).first() if cidade_id else None


def _renderizar(request, template, contexto):
    """render() com a cidade escolhida, que aparece no topo de todas as páginas."""
    contexto["cidade_atual"] = _cidade_atual(request)
    return render(request, template, contexto)


def _paginar(request, imoveis):
    """Uma página de cartões, já passados pelo serializer público do cartão."""
    pagina = Paginator(imoveis, IMOVEIS_POR_PAGINA).get_page(request.GET.get("page"))
    cartoes = ImovelVitrineSerializer(pagina.object_list, many=True, context={"request": request}).data
    return pagina, cartoes


class RedirecionarContato(HttpResponseRedirect):
    """Redirecionamento que aceita tel: e mailto: além do https (o padrão do Django recusa)."""

    allowed_schemes = ["https", "tel", "mailto"]


def inicio(request):
    return _renderizar(request, "site_publico/inicio.html", {"cidades": cidades_atendidas()})


def vitrine(request, cidade_id):
    cidade = get_object_or_404(Cidade, pk=cidade_id)
    request.session[CHAVE_CIDADE] = cidade.pk

    # as caixinhas de característica chegam repetidas (?caracteristicas=1&caracteristicas=3);
    # a busca da vitrine espera "1,3", como na API
    params = request.GET.copy()
    selecionadas = request.GET.getlist("caracteristicas")
    params["caracteristicas"] = ",".join(selecionadas)

    erros = {}
    try:
        imoveis = buscar_na_vitrine(cidade, params)
    except FiltroInvalido as erro:
        erros = erro.erros
        imoveis = Imovel.objects.none()

    pagina, cartoes = _paginar(request, imoveis)
    sem_pagina = request.GET.copy()
    sem_pagina.pop("page", None)

    return _renderizar(request, "site_publico/vitrine.html", {
        "cidade": cidade,
        "imoveis": cartoes,
        "pagina": pagina,
        "query_sem_pagina": sem_pagina.urlencode(),
        "erros": erros,
        "filtros": request.GET,
        "caracteristicas_selecionadas": selecionadas,
        "caracteristicas": Caracteristica.objects.all(),
        "finalidades": Imovel.Finalidade.choices,
        "naturezas": Imovel.Natureza.choices,
        "ordenacoes": ROTULOS_ORDENACAO.items(),
    })


def imovel(request, pk):
    # rascunho e empresa inativa não estão em imoveis_da_vitrine() — caem em 404
    objeto = get_object_or_404(
        imoveis_da_vitrine().select_related(
            "empresa", "endereco__cidade", "corretor_responsavel__empresa", "casa", "terreno",
            "apartamento__predio__endereco__cidade", "lote__quadra__loteamento",
        ).prefetch_related(
            # capa primeiro e depois a ordem do corretor — a mesma sequência do detalhe da API
            # pública que o app usa (APP 11)
            Prefetch("fotos", queryset=FotoImovel.objects.order_by("-capa", "ordem", "id")),
            "caracteristicas",
        ),
        pk=pk,
    )
    dados = ImovelVitrineDetalheSerializer(objeto, context={"request": request}).data

    # só os nomes dos canais que têm destino; o link em si (com o código interno
    # do imóvel na mensagem) só é montado no POST de contato, nunca vai para o HTML
    canais = canais_disponiveis(objeto)

    return _renderizar(request, "site_publico/imovel.html", {
        "imovel": dados,
        "corretor": dados["corretor_responsavel"],
        "empresa_id": objeto.empresa_id,
        "empresa_nome": objeto.empresa.razao_social_ou_nome,
        "canais": canais,
    })


@require_POST
def contato(request, pk):
    """Grava o contato e redireciona para o canal escolhido (W11).

    POST e não GET: robôs, pré-carregamento do navegador e prévias de link
    fazem GET sozinhos — só um clique de verdade envia o formulário.
    """
    objeto = get_object_or_404(
        imoveis_da_vitrine().select_related("empresa", "corretor_responsavel__empresa"), pk=pk
    )
    url_do_anuncio = request.build_absolute_uri(reverse("site_publico:imovel", args=[objeto.pk]))
    try:
        _, link = registrar_contato(objeto, request.POST.get("canal"), url_do_anuncio)
    except CanalIndisponivel as erro:
        return HttpResponseBadRequest(str(erro))
    return RedirecionarContato(link)


def empresa(request, pk):
    objeto = get_object_or_404(Empresa, pk=pk, ativa=True)
    dados = EmpresaPublicaSerializer(objeto, context={"request": request}).data
    pagina, cartoes = _paginar(request, imoveis_da_empresa(objeto))
    return _renderizar(request, "site_publico/empresa.html", {
        "empresa": dados,
        "imoveis": cartoes,
        "pagina": pagina,
        "whatsapp": link_whatsapp(dados["whatsapp"], "Olá! Vi o perfil de vocês no Imóveis Aqui."),
    })


def corretor(request, pk):
    # corretor desativado ou de empresa inativa não tem perfil público
    objeto = get_object_or_404(
        Usuario.objects.select_related("empresa"),
        pk=pk, perfil=Usuario.Perfil.CORRETOR, is_active=True, empresa__ativa=True,
    )
    dados = CorretorPublicoSerializer(objeto, context={"request": request}).data
    pagina, cartoes = _paginar(request, imoveis_do_corretor(objeto))
    return _renderizar(request, "site_publico/corretor.html", {
        "corretor": dados,
        "empresa_nome": objeto.empresa.razao_social_ou_nome,
        "imoveis": cartoes,
        "pagina": pagina,
        "whatsapp": link_whatsapp(dados["whatsapp"], "Olá! Vi seu perfil no Imóveis Aqui."),
    })
