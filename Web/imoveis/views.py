from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DetailView, ListView

from contas.models import Usuario
from contas.views import CorretorRequiredMixin
from core.validators import validar_imagem

from .forms import EnderecoForm, ImovelForm
from .models import FotoImovel, Imovel


def corretor_required(view_func):
    @login_required
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if request.user.perfil != Usuario.Perfil.CORRETOR:
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return wrapper


class ImovelListView(CorretorRequiredMixin, ListView):
    template_name = "imoveis/imovel_list.html"
    context_object_name = "imoveis"

    def get_queryset(self):
        return Imovel.objects.filter(empresa=self.request.user.empresa).prefetch_related("fotos")


class ImovelCreateView(CorretorRequiredMixin, CreateView):
    form_class = ImovelForm
    template_name = "imoveis/imovel_form.html"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["empresa"] = self.request.user.empresa
        return kwargs

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
        messages.success(self.request, "Imóvel cadastrado. Agora complete o endereço e as fotos.")
        return redirect("imoveis:imovel_detail", pk=imovel.pk)


class ImovelDetailView(CorretorRequiredMixin, DetailView):
    template_name = "imoveis/imovel_detail.html"
    context_object_name = "imovel"

    def get_queryset(self):
        return Imovel.objects.filter(empresa=self.request.user.empresa)

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["endereco_form"] = EnderecoForm(instance=self.object.endereco)
        return contexto


@corretor_required
@require_POST
def imovel_atualizar_endereco(request, pk):
    imovel = get_object_or_404(Imovel, pk=pk, empresa=request.user.empresa)
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


@corretor_required
@require_POST
def imovel_publicar(request, pk):
    imovel = get_object_or_404(Imovel, pk=pk, empresa=request.user.empresa)
    try:
        imovel.publicar()
        messages.success(request, "Imóvel publicado.")
    except ValidationError as erro:
        messages.error(request, erro.messages[0])
    return redirect("imoveis:imovel_detail", pk=pk)
