from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.contrib.auth.decorators import login_required
from django.http import QueryDict
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.views.generic import ListView

from contas.models import Usuario
from imoveis.models import Imovel
from imoveis.vitrine import FiltroInvalido

from .models import Contato
from .servicos import contatos_visiveis_para, filtrar_contatos

PERFIS_COM_CAIXA = (Usuario.Perfil.CORRETOR, Usuario.Perfil.GESTOR)


class CorretorOuGestorRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Caixa de contatos: corretor (os dele) e gestor (os da empresa). Administrador não entra."""

    def test_func(self):
        return self.request.user.perfil in PERFIS_COM_CAIXA


class ContatoListView(CorretorOuGestorRequiredMixin, ListView):
    template_name = "contatos/contato_list.html"
    context_object_name = "contatos"
    paginate_by = 50

    def get_queryset(self):
        self.erros = {}
        queryset = contatos_visiveis_para(self.request.user).select_related("imovel", "corretor")
        try:
            return filtrar_contatos(queryset, self.request.GET)
        except FiltroInvalido as erro:
            self.erros = erro.erros
            return queryset.none()

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        sem_pagina = self.request.GET.copy()
        sem_pagina.pop("page", None)
        contexto.update({
            "erros": self.erros,
            "filtros": self.request.GET,
            "query_atual": self.request.GET.urlencode(),
            "query_sem_pagina": sem_pagina.urlencode(),
            # só os imóveis que aparecem nos contatos que este usuário pode ver
            "imoveis": Imovel.objects.filter(
                pk__in=contatos_visiveis_para(self.request.user).values("imovel")
            ).order_by("codigo"),
            "retornos": [(valor, Contato.Retorno(valor).label) for valor in Contato.RETORNOS_ANOTAVEIS],
            "eh_gestor": self.request.user.perfil == Usuario.Perfil.GESTOR,
        })
        return contexto


@login_required
@require_POST
def contato_anotar_retorno(request, pk):
    """Corretor (dos contatos dele) ou gestor (da empresa) anota o retorno.

    O contato é buscado dentro de contatos_visiveis_para(): de outro corretor
    ou de outra empresa cai em 404, sem revelar que existe.
    """
    if request.user.perfil not in PERFIS_COM_CAIXA:
        raise PermissionDenied
    contato = get_object_or_404(contatos_visiveis_para(request.user), pk=pk)
    try:
        contato.anotar_retorno(request.POST.get("retorno"))
        messages.success(request, f"Retorno anotado: {contato.get_retorno_display().lower()}.")
    except ValidationError as erro:
        messages.error(request, erro.messages[0])

    # volta para a caixa com os mesmos filtros (só a query string; o caminho é sempre o nosso)
    filtros = QueryDict(request.POST.get("filtros", "")).urlencode()
    destino = reverse("contatos:contato_list")
    return redirect(f"{destino}?{filtros}" if filtros else destino)
