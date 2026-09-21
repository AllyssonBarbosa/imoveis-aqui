from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import CreateView, ListView

from contas.views import CorretorRequiredMixin
from core.validators import somente_digitos

from .forms import ProprietarioForm
from .models import Proprietario


class ProprietarioListView(CorretorRequiredMixin, ListView):
    template_name = "proprietarios/proprietario_list.html"
    context_object_name = "proprietarios"

    def get_queryset(self):
        queryset = Proprietario.objects.filter(empresa=self.request.user.empresa)
        busca = self.request.GET.get("busca", "").strip()
        if busca:
            filtro = Q(nome_razao_social__icontains=busca)
            documento_busca = somente_digitos(busca)
            if documento_busca:
                filtro |= Q(documento__icontains=documento_busca)
            queryset = queryset.filter(filtro)
        return queryset

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["busca"] = self.request.GET.get("busca", "")
        return contexto


class ProprietarioCreateView(CorretorRequiredMixin, CreateView):
    form_class = ProprietarioForm
    template_name = "proprietarios/proprietario_form.html"
    success_url = reverse_lazy("proprietarios:proprietario_list")

    def form_valid(self, form):
        proprietario = form.save(commit=False)
        proprietario.empresa = self.request.user.empresa
        try:
            proprietario.full_clean()
        except ValidationError as erro:
            form._update_errors(erro)
            return self.form_invalid(form)
        proprietario.save()
        messages.success(self.request, "Proprietário cadastrado com sucesso.")
        return redirect(self.success_url)
