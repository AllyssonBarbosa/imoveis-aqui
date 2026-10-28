from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django.db import transaction
from django.views.generic import CreateView, DetailView, ListView

from contas.models import Usuario
from contas.views import CorretorRequiredMixin, GestorRequiredMixin
from core.validators import validar_imagem

from .forms import (
    DocumentoImovelForm,
    EnderecoForm,
    FiltroImovelForm,
    ImovelForm,
    LoteamentoForm,
    PredioForm,
    QuadraForm,
    SituacaoForm,
    form_de_detalhes,
)
from .models import DocumentoImovel, FotoImovel, Imovel, Loteamento, Predio, Quadra


def corretor_required(view_func):
    @login_required
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if request.user.perfil != Usuario.Perfil.CORRETOR:
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return wrapper


def gestor_required(view_func):
    @login_required
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if request.user.perfil != Usuario.Perfil.GESTOR:
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return wrapper


class FiltroImovelMixin:
    """Lista de imóveis da empresa com o formulário de filtros (FiltroImovelForm)."""

    campos_de_filtro = []

    def get_filtro(self):
        if not hasattr(self, "_filtro"):
            self._filtro = FiltroImovelForm(
                self.request.GET or None, empresa=self.request.user.empresa,
                campos=self.campos_de_filtro,
            )
        return self._filtro

    def filtrar(self, queryset):
        return self.get_filtro().filtrar(queryset)

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["filtro"] = self.get_filtro()
        return contexto


class ImovelListView(CorretorRequiredMixin, FiltroImovelMixin, ListView):
    template_name = "imoveis/imovel_list.html"
    context_object_name = "imoveis"
    campos_de_filtro = ["busca", "natureza", "finalidade", "situacao", "vitrine"]

    def get_queryset(self):
        queryset = Imovel.objects.filter(empresa=self.request.user.empresa).prefetch_related("fotos")
        return self.filtrar(queryset)


class ImovelCreateView(CorretorRequiredMixin, CreateView):
    form_class = ImovelForm
    template_name = "imoveis/imovel_form.html"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["empresa"] = self.request.user.empresa
        return kwargs

    def get_initial(self):
        # atalho "+ Novo lote" da tela do loteamento já chega com a natureza escolhida
        inicial = super().get_initial()
        natureza = self.request.GET.get("natureza")
        if natureza in Imovel.Natureza.values:
            inicial["natureza"] = natureza
            if natureza == Imovel.Natureza.LOTE:
                inicial["finalidade"] = Imovel.Finalidade.VENDA
        return inicial

    def form_valid(self, form):
        imovel = form.save(commit=False)
        imovel.empresa = self.request.user.empresa
        try:
            imovel.full_clean(exclude=["endereco"])
        except ValidationError as erro:
            form._update_errors(erro)
            return self.form_invalid(form)
        imovel.save()
        form.save_m2m()
        messages.success(
            self.request,
            f"Imóvel cadastrado. Agora complete os dados da {imovel.get_natureza_display().lower()}, "
            "o endereço e as fotos.",
        )
        return redirect("imoveis:imovel_detail", pk=imovel.pk)


class ImovelDetailView(CorretorRequiredMixin, DetailView):
    template_name = "imoveis/imovel_detail.html"
    context_object_name = "imovel"

    def get_queryset(self):
        return Imovel.objects.filter(empresa=self.request.user.empresa)

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["endereco_form"] = EnderecoForm(instance=self.object.endereco)
        contexto["detalhes_form"] = form_de_detalhes(self.object)
        return contexto


@corretor_required
@require_POST
def imovel_atualizar_detalhes(request, pk):
    """Salva os dados próprios da natureza (casa ou terreno).

    Com erro, a tela é renderizada de novo com o formulário preenchido,
    pra mensagem aparecer ao lado do campo (ex.: área construída > terreno).
    """
    imovel = get_object_or_404(Imovel, pk=pk, empresa=request.user.empresa)
    form = form_de_detalhes(imovel, request.POST)
    if form.is_valid():
        form.save()
        messages.success(request, f"Dados da {imovel.get_natureza_display().lower()} salvos.")
        return redirect("imoveis:imovel_detail", pk=pk)

    messages.error(request, "Não deu pra salvar: confira os campos destacados.")
    return render(request, "imoveis/imovel_detail.html", {
        "imovel": imovel,
        "endereco_form": EnderecoForm(instance=imovel.endereco),
        "detalhes_form": form,
    })


@corretor_required
@require_POST
def imovel_atualizar_endereco(request, pk):
    imovel = get_object_or_404(Imovel, pk=pk, empresa=request.user.empresa)
    if imovel.natureza == Imovel.Natureza.APARTAMENTO:
        messages.error(request, "O endereço do apartamento é o do prédio — corrija pelo cadastro do prédio.")
        return redirect("imoveis:imovel_detail", pk=pk)
    form = EnderecoForm(request.POST, instance=imovel.endereco)
    if form.is_valid():
        endereco = form.save()
        if not imovel.endereco_id:
            imovel.endereco = endereco
            imovel.save(update_fields=["endereco"])
        messages.success(request, "Endereço salvo.")
    else:
        messages.error(request, "Não deu pra salvar o endereço: confira os campos.")
    return redirect("imoveis:imovel_detail", pk=pk)


@corretor_required
@require_POST
def imovel_upload_fotos(request, pk):
    imovel = get_object_or_404(Imovel, pk=pk, empresa=request.user.empresa)
    arquivos = request.FILES.getlist("fotos")
    if not arquivos:
        messages.error(request, "Escolha ao menos uma foto.")
        return redirect("imoveis:imovel_detail", pk=pk)

    proxima_ordem = imovel.fotos.count()
    for indice, arquivo in enumerate(arquivos):
        try:
            validar_imagem(arquivo)
        except ValidationError as erro:
            messages.error(request, f"{arquivo.name}: {erro.messages[0]}")
            continue
        FotoImovel.objects.create(imovel=imovel, imagem=arquivo, ordem=proxima_ordem + indice)
    messages.success(request, "Fotos enviadas.")
    return redirect("imoveis:imovel_detail", pk=pk)


@corretor_required
@require_POST
def imovel_definir_capa(request, pk, foto_id):
    imovel = get_object_or_404(Imovel, pk=pk, empresa=request.user.empresa)
    foto = get_object_or_404(FotoImovel, pk=foto_id, imovel=imovel)
    foto.definir_como_capa()
    messages.success(request, "Foto de capa atualizada.")
    return redirect("imoveis:imovel_detail", pk=pk)


@corretor_required
@require_POST
def imovel_excluir_foto(request, pk, foto_id):
    imovel = get_object_or_404(Imovel, pk=pk, empresa=request.user.empresa)
    get_object_or_404(FotoImovel, pk=foto_id, imovel=imovel).delete()
    messages.success(request, "Foto removida.")
    return redirect("imoveis:imovel_detail", pk=pk)


class PredioListView(CorretorRequiredMixin, ListView):
    template_name = "imoveis/predio_list.html"
    context_object_name = "predios"

    def get_queryset(self):
        return Predio.objects.filter(empresa=self.request.user.empresa).select_related(
            "endereco__cidade"
        )


def _salvar_predio(request, predio=None):
    """Cadastro e edição do prédio — os dados do prédio e o endereço na mesma tela."""
    predio_form = PredioForm(request.POST or None, instance=predio)
    endereco_form = EnderecoForm(
        request.POST or None, instance=predio.endereco if predio else None, prefix="endereco"
    )

    if request.method == "POST" and predio_form.is_valid() and endereco_form.is_valid():
        novo = predio_form.save(commit=False)
        novo.empresa = request.user.empresa
        try:
            novo.full_clean(exclude=["endereco"])
        except ValidationError as erro:
            predio_form._update_errors(erro)
        else:
            with transaction.atomic():
                novo.endereco = endereco_form.save()
                novo.save()
            if predio:
                messages.success(
                    request, "Prédio atualizado — os anúncios de todos os apartamentos já mostram a mudança."
                )
            else:
                messages.success(request, "Prédio cadastrado. Agora é só ligar os apartamentos a ele.")
            return redirect("imoveis:predio_list")

    return render(request, "imoveis/predio_form.html", {
        "predio": predio,
        "form": predio_form,
        "endereco_form": endereco_form,
    })


@corretor_required
def predio_create(request):
    return _salvar_predio(request)


@corretor_required
def predio_update(request, pk):
    predio = get_object_or_404(Predio, pk=pk, empresa=request.user.empresa)
    return _salvar_predio(request, predio)


class LoteamentoListView(CorretorRequiredMixin, ListView):
    template_name = "imoveis/loteamento_list.html"
    context_object_name = "loteamentos"

    def get_queryset(self):
        return Loteamento.objects.filter(empresa=self.request.user.empresa).select_related("cidade")


def _salvar_loteamento(request, loteamento=None):
    form = LoteamentoForm(request.POST or None, instance=loteamento)
    if request.method == "POST" and form.is_valid():
        novo = form.save(commit=False)
        novo.empresa = request.user.empresa
        try:
            novo.full_clean()
        except ValidationError as erro:
            form._update_errors(erro)
        else:
            novo.save()
            if loteamento:
                messages.success(request, "Loteamento atualizado — vale para todos os lotes dele.")
            else:
                messages.success(request, "Loteamento cadastrado. Agora cadastre as quadras.")
            return redirect("imoveis:loteamento_detail", pk=novo.pk)

    return render(request, "imoveis/loteamento_form.html", {"loteamento": loteamento, "form": form})


@corretor_required
def loteamento_create(request):
    return _salvar_loteamento(request)


@corretor_required
def loteamento_update(request, pk):
    loteamento = get_object_or_404(Loteamento, pk=pk, empresa=request.user.empresa)
    return _salvar_loteamento(request, loteamento)


@corretor_required
def loteamento_detail(request, pk):
    """Infraestrutura do loteamento e, por quadra, lotes disponíveis × vendidos."""
    loteamento = get_object_or_404(
        Loteamento.objects.select_related("cidade"), pk=pk, empresa=request.user.empresa
    )
    quadra_form = QuadraForm(request.POST or None)

    if request.method == "POST":
        if quadra_form.is_valid():
            quadra = quadra_form.save(commit=False)
            quadra.loteamento = loteamento
            try:
                quadra.full_clean()
            except ValidationError as erro:
                quadra_form._update_errors(erro)
            else:
                quadra.save()
                messages.success(request, f"Quadra {quadra.identificacao} cadastrada.")
                return redirect("imoveis:loteamento_detail", pk=pk)

    quadras = loteamento.quadras.prefetch_related("lotes__imovel")  # vendido lê a situação do imóvel
    return render(request, "imoveis/loteamento_detail.html", {
        "loteamento": loteamento,
        "quadra_form": quadra_form,
        "quadras": [(quadra, quadra.resumo_lotes()) for quadra in quadras],
    })


# ---------------------------------------------------------------- gestão (gestor, W08)
# Documentos, situação e publicação são só do gestor. O corretor cadastra o
# imóvel, mas quem decide o que vai pra vitrine é o gestor.


class GestaoImovelListView(GestorRequiredMixin, FiltroImovelMixin, ListView):
    template_name = "imoveis/gestao_list.html"
    context_object_name = "imoveis"
    campos_de_filtro = ["busca", "natureza", "corretor", "situacao", "vitrine"]

    def get_queryset(self):
        queryset = Imovel.objects.filter(empresa=self.request.user.empresa).select_related(
            "corretor_responsavel"
        )
        return self.filtrar(queryset)


def _imovel_da_empresa(request, pk):
    return get_object_or_404(Imovel, pk=pk, empresa=request.user.empresa)


def _tela_gestao(request, imovel, situacao_form=None, documento_form=None, status=200):
    return render(request, "imoveis/gestao_detail.html", {
        "imovel": imovel,
        "pendencias": imovel.pendencias_para_publicar(),
        "situacao_form": situacao_form or SituacaoForm(),
        "documento_form": documento_form or DocumentoImovelForm(),
        "historico": imovel.historico_situacao.select_related("alterado_por"),
        "documentos": imovel.documentos.select_related("enviado_por"),
    }, status=status)


@gestor_required
def gestao_imovel_detail(request, pk):
    return _tela_gestao(request, _imovel_da_empresa(request, pk))


@gestor_required
@require_POST
def gestao_imovel_publicar(request, pk):
    imovel = _imovel_da_empresa(request, pk)
    try:
        imovel.publicar()
    except ValidationError as erro:
        messages.error(request, erro.messages[0])
    else:
        if imovel.situacao == Imovel.Situacao.DISPONIVEL:
            messages.success(request, "Imóvel publicado — já aparece na vitrine.")
        else:
            messages.success(
                request,
                f"Imóvel publicado, mas só aparece na vitrine quando voltar a ficar disponível "
                f"(hoje está {imovel.get_situacao_display().lower()}).",
            )
    return redirect("imoveis:gestao_imovel_detail", pk=pk)


@gestor_required
@require_POST
def gestao_imovel_despublicar(request, pk):
    imovel = _imovel_da_empresa(request, pk)
    imovel.despublicar()
    messages.success(request, "Imóvel tirado da vitrine.")
    return redirect("imoveis:gestao_imovel_detail", pk=pk)


@gestor_required
@require_POST
def gestao_imovel_situacao(request, pk):
    imovel = _imovel_da_empresa(request, pk)
    form = SituacaoForm(request.POST)
    if form.is_valid():
        try:
            imovel.mudar_situacao(
                form.cleaned_data["situacao"], request.user, form.cleaned_data["observacao"]
            )
        except ValidationError as erro:
            form.add_error("situacao", erro.messages[0])
        else:
            messages.success(request, f"Situação alterada para {imovel.get_situacao_display().lower()}.")
            return redirect("imoveis:gestao_imovel_detail", pk=pk)
    messages.error(request, "Não deu pra mudar a situação: confira o formulário.")
    return _tela_gestao(request, imovel, situacao_form=form)


@gestor_required
@require_POST
def gestao_documento_enviar(request, pk):
    imovel = _imovel_da_empresa(request, pk)
    form = DocumentoImovelForm(request.POST, request.FILES)
    form.instance.imovel = imovel
    form.instance.enviado_por = request.user
    if form.is_valid():
        documento = form.save()
        messages.success(request, f"{documento.get_tipo_display()} anexado.")
        return redirect("imoveis:gestao_imovel_detail", pk=pk)
    messages.error(request, "Não deu pra anexar o documento: confira os campos.")
    return _tela_gestao(request, imovel, documento_form=form)


@gestor_required
def gestao_documento_baixar(request, pk, documento_id):
    # o arquivo só sai por aqui, depois de conferir que o imóvel é da empresa do gestor
    imovel = _imovel_da_empresa(request, pk)
    documento = get_object_or_404(DocumentoImovel, pk=documento_id, imovel=imovel)
    return FileResponse(documento.arquivo.open("rb"), as_attachment=True)


@gestor_required
@require_POST
def gestao_documento_excluir(request, pk, documento_id):
    imovel = _imovel_da_empresa(request, pk)
    documento = get_object_or_404(DocumentoImovel, pk=documento_id, imovel=imovel)
    documento.arquivo.delete(save=False)
    documento.delete()
    messages.success(request, "Documento removido.")
    return redirect("imoveis:gestao_imovel_detail", pk=pk)
