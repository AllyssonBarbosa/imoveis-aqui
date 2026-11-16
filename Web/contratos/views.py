from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import ListView

from contas.models import Usuario
from contas.views import CorretorOuGestorRequiredMixin, GestorRequiredMixin

from .forms import ContratoForm, FiltroContratoForm
from .models import Contrato
from .servicos import contratos_visiveis_para, salvar_contrato


class ContratoListView(CorretorOuGestorRequiredMixin, ListView):
    """Gestor vê todos os contratos da empresa; corretor, só aqueles em que é o corretor."""

    template_name = "contratos/contrato_list.html"
    context_object_name = "contratos"
    paginate_by = 50

    def get_queryset(self):
        self.filtro = FiltroContratoForm(self.request.GET)
        queryset = contratos_visiveis_para(self.request.user).select_related("imovel", "cliente", "corretor")
        return self.filtro.filtrar(queryset)

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["filtro"] = self.filtro
        return contexto


class ContratoDetailView(CorretorOuGestorRequiredMixin, View):
    """Dados do contrato e todas as parcelas. Contrato de fora (ou de outro corretor) cai em 404."""

    def get(self, request, pk):
        contrato = get_object_or_404(
            contratos_visiveis_para(request.user).select_related(
                "imovel", "cliente", "corretor", "criado_por", "assinado_por"
            ),
            pk=pk,
        )
        parcelas = contrato.parcelas.all()
        return render(request, "contratos/contrato_detail.html", {
            "contrato": contrato,
            "parcelas": parcelas,
            "total_parcelas": parcelas.aggregate(total=Sum("valor"))["total"] or 0,
            "eh_gestor": request.user.perfil == Usuario.Perfil.GESTOR,
        })


class ContratoFormView(GestorRequiredMixin, View):
    """Cadastro (sem pk) e edição (com pk) do contrato — só o gestor."""

    template_name = "contratos/contrato_form.html"

    def _contrato(self, pk):
        if pk is None:
            return Contrato(empresa=self.request.user.empresa)
        return get_object_or_404(contratos_visiveis_para(self.request.user), pk=pk)

    def _tela(self, form, contrato, status=200):
        return render(self.request, self.template_name, {"form": form, "contrato": contrato}, status=status)

    def get(self, request, pk=None):
        contrato = self._contrato(pk)
        return self._tela(ContratoForm(instance=contrato, empresa=request.user.empresa), contrato)

    def post(self, request, pk=None):
        contrato = self._contrato(pk)
        form = ContratoForm(request.POST, instance=contrato, empresa=request.user.empresa)
        if form.is_valid():
            contrato = form.save(commit=False)
            contrato.empresa = request.user.empresa  # nunca vem do formulário
            try:
                salvar_contrato(contrato, request.user)
            except ValidationError as erro:
                form._update_errors(erro)
            else:
                if pk is None:
                    messages.success(
                        request, f"Contrato cadastrado. {contrato.parcelas.count()} parcelas geradas."
                    )
                else:
                    messages.success(request, "Contrato atualizado.")
                return redirect("contratos:contrato_detail", pk=contrato.pk)
        return self._tela(form, contrato)
