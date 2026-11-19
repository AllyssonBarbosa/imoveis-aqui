from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View
from django.views.decorators.http import require_POST
from django.views.generic import ListView

from contas.models import Usuario
from contas.views import CorretorOuGestorRequiredMixin, GestorRequiredMixin
from imoveis.views import gestor_required

from .arquivos import responder_download
from .forms import AssinaturaForm, BaixaForm, ContratoForm, DocumentoContratoForm, FiltroContratoForm
from .models import Baixa, Contrato, Parcela
from .servicos import assinar_contrato, contratos_visiveis_para, registrar_baixa, salvar_contrato


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
        parcelas = list(contrato.parcelas.com_situacao())
        atrasadas = [p for p in parcelas if p.situacao == Parcela.Situacao.ATRASADA]
        return render(request, "contratos/contrato_detail.html", {
            "contrato": contrato,
            "parcelas": parcelas,
            "atrasadas": len(atrasadas),
            "saldo_em_atraso": sum((p.saldo for p in atrasadas), Decimal("0")),
            "total_parcelas": sum((p.valor for p in parcelas), Decimal("0")),
            "eh_gestor": request.user.perfil == Usuario.Perfil.GESTOR,
            "assinatura_form": AssinaturaForm(initial={"data_assinatura": timezone.localdate()}),
            "documentos": contrato.documentos.select_related("enviado_por"),
            "documento_form": DocumentoContratoForm(),
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


@gestor_required
@require_POST
def contrato_assinar(request, pk):
    """Só o gestor assina. Contrato de outra empresa cai em 404."""
    contrato = get_object_or_404(contratos_visiveis_para(request.user), pk=pk)
    form = AssinaturaForm(request.POST)
    if not form.is_valid():
        messages.error(request, form.errors["data_assinatura"][0])
        return redirect("contratos:contrato_detail", pk=pk)
    try:
        contrato = assinar_contrato(contrato, request.user, form.cleaned_data["data_assinatura"])
    except ValidationError as erro:
        messages.error(request, erro.messages[0])
    else:
        messages.success(
            request,
            f"Contrato assinado. O imóvel {contrato.imovel.codigo} agora está "
            f"{contrato.imovel.get_situacao_display().lower()} e saiu da vitrine.",
        )
    return redirect("contratos:contrato_detail", pk=pk)


# --- documentos do contrato (arquivos privados) --------------------------------


def _contrato_visivel(request, pk):
    """Contrato que o usuário pode ver (empresa + perfil). De fora cai em 404, sem revelar que existe."""
    return get_object_or_404(contratos_visiveis_para(request.user), pk=pk)


@gestor_required
@require_POST
def documento_enviar(request, pk):
    contrato = _contrato_visivel(request, pk)
    form = DocumentoContratoForm(request.POST, request.FILES)
    form.instance.contrato = contrato
    form.instance.empresa_id = contrato.empresa_id
    form.instance.enviado_por = request.user
    if form.is_valid():
        documento = form.save()
        messages.success(request, f"{documento.get_tipo_display()} anexado.")
    else:
        erros = [erro for lista in form.errors.values() for erro in lista]
        messages.error(request, f"Não deu pra anexar o documento: {' '.join(erros)}")
    return redirect("contratos:contrato_detail", pk=pk)


@login_required
def documento_baixar(request, pk, documento_id):
    """Gestor da empresa ou corretor do contrato. O arquivo só sai por aqui."""
    if request.user.perfil not in (Usuario.Perfil.GESTOR, Usuario.Perfil.CORRETOR):
        raise PermissionDenied
    contrato = _contrato_visivel(request, pk)
    documento = get_object_or_404(contrato.documentos, pk=documento_id)
    return responder_download(documento.arquivo, documento.nome_para_download)


@gestor_required
@require_POST
def documento_excluir(request, pk, documento_id):
    contrato = _contrato_visivel(request, pk)
    documento = get_object_or_404(contrato.documentos, pk=documento_id)
    documento.arquivo.delete(save=False)  # apaga o arquivo do disco, não só o registro
    documento.delete()
    messages.success(request, "Documento removido.")
    return redirect("contratos:contrato_detail", pk=pk)


# --- parcela e baixas (critério 4) ------------------------------------------------


class ParcelaDetailView(CorretorOuGestorRequiredMixin, View):
    """Parcela com as baixas. GET: gestor e corretor do contrato. POST (registrar baixa): só o gestor."""

    template_name = "contratos/parcela_detail.html"

    def _parcela(self, pk, parcela_id):
        contrato = _contrato_visivel(self.request, pk)
        return get_object_or_404(contrato.parcelas.com_situacao().select_related("contrato"), pk=parcela_id)

    def _tela(self, parcela, form):
        eh_gestor = self.request.user.perfil == Usuario.Perfil.GESTOR
        return render(self.request, self.template_name, {
            "contrato": parcela.contrato,
            "parcela": parcela,
            "baixas": parcela.baixas.select_related("registrada_por"),
            "eh_gestor": eh_gestor,
            "pode_dar_baixa": eh_gestor and parcela.contrato.assinado,
            "form": form,
        })

    def get(self, request, pk, parcela_id):
        parcela = self._parcela(pk, parcela_id)
        form = BaixaForm(initial={"data_pagamento": timezone.localdate(), "valor_pago": parcela.saldo or None})
        return self._tela(parcela, form)

    def post(self, request, pk, parcela_id):
        parcela = self._parcela(pk, parcela_id)  # de fora: 404 antes de checar o perfil
        if request.user.perfil != Usuario.Perfil.GESTOR:
            raise PermissionDenied
        form = BaixaForm(request.POST, request.FILES, instance=Baixa(parcela=parcela))
        form.instance.empresa_id = parcela.empresa_id
        form.instance.registrada_por = request.user
        if form.is_valid():
            try:
                registrar_baixa(form.save(commit=False), request.user)
            except ValidationError as erro:
                form._update_errors(erro)
            else:
                messages.success(request, "Baixa registrada.")
                return redirect("contratos:parcela_detail", pk=pk, parcela_id=parcela_id)
        return self._tela(parcela, form)


@login_required
def comprovante_baixar(request, pk, parcela_id, baixa_id):
    """Gestor da empresa ou corretor do contrato. O comprovante só sai por aqui."""
    if request.user.perfil not in (Usuario.Perfil.GESTOR, Usuario.Perfil.CORRETOR):
        raise PermissionDenied
    contrato = _contrato_visivel(request, pk)
    baixa = get_object_or_404(
        Baixa.objects.select_related("parcela"), pk=baixa_id, parcela_id=parcela_id, parcela__contrato=contrato
    )
    return responder_download(baixa.comprovante, baixa.nome_para_download)
