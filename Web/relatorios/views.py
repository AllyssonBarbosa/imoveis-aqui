from django.views.generic import TemplateView

from contas.views import GestorRequiredMixin
from imoveis.vitrine import FiltroInvalido

from .servicos import (
    DIAS_A_VENCER_MAXIMO, acervo_por_natureza_e_situacao, contatos_por_corretor, ler_dias,
    parcelas_em_atraso_e_a_vencer,
)


class RelatorioView(GestorRequiredMixin, TemplateView):
    """Base dos relatórios: só o gestor, sempre com a empresa dele."""

    @property
    def empresa_id(self):
        return self.request.user.empresa_id


class RelatoriosIndexView(RelatorioView):
    template_name = "relatorios/index.html"


class AcervoView(RelatorioView):
    template_name = "relatorios/acervo.html"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["relatorio"] = acervo_por_natureza_e_situacao(self.empresa_id)
        return contexto


class ContatosView(RelatorioView):
    template_name = "relatorios/contatos.html"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["filtros"] = self.request.GET
        try:
            contexto["relatorio"] = contatos_por_corretor(self.empresa_id, self.request.GET)
        except FiltroInvalido as erro:
            contexto["erros"] = erro.erros
        return contexto


class ParcelasView(RelatorioView):
    template_name = "relatorios/parcelas.html"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["dias_maximo"] = DIAS_A_VENCER_MAXIMO
        try:
            dias = ler_dias(self.request.GET.get("dias"))
        except FiltroInvalido as erro:
            contexto["erros"] = erro.erros
            return contexto
        contexto["relatorio"] = parcelas_em_atraso_e_a_vencer(self.empresa_id, dias)
        return contexto
