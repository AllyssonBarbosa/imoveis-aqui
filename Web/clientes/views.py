from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import CreateView, ListView, UpdateView

from contas.views import CorretorOuGestorRequiredMixin

from .forms import ClienteForm
from .servicos import buscar_clientes, clientes_da_empresa


class ClienteListView(CorretorOuGestorRequiredMixin, ListView):
    template_name = "clientes/cliente_list.html"
    context_object_name = "clientes"
    paginate_by = 50

    def get_queryset(self):
        return buscar_clientes(clientes_da_empresa(self.request.user), self.request.GET.get("busca"))

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["busca"] = self.request.GET.get("busca", "")
        return contexto


class ClienteFormMixin:
    """Validação comum do cadastro e da edição.

    A empresa nunca vem do formulário: é sempre a do usuário logado. O
    full_clean() roda depois de definir a empresa, para que a checagem de
    "documento repetido nesta empresa" apareça como erro no próprio campo.
    """

    form_class = ClienteForm
    template_name = "clientes/cliente_form.html"
    success_url = reverse_lazy("clientes:cliente_list")
    mensagem_sucesso = ""

    def form_valid(self, form):
        cliente = form.save(commit=False)
        cliente.empresa = self.request.user.empresa
        try:
            cliente.full_clean()
        except ValidationError as erro:
            form._update_errors(erro)
            return self.form_invalid(form)
        cliente.save()
        messages.success(self.request, self.mensagem_sucesso)
        return redirect(self.success_url)


class ClienteCreateView(CorretorOuGestorRequiredMixin, ClienteFormMixin, CreateView):
    mensagem_sucesso = "Cliente cadastrado com sucesso."


class ClienteUpdateView(CorretorOuGestorRequiredMixin, ClienteFormMixin, UpdateView):
    mensagem_sucesso = "Cliente atualizado com sucesso."

    def get_queryset(self):
        # cliente de outra empresa cai em 404, sem revelar que existe
        return clientes_da_empresa(self.request.user)
