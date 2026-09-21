from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth.views import LoginView, LogoutView
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, ListView

from .forms import CorretorForm
from .models import Usuario


class PainelLoginView(LoginView):
    template_name = "contas/login.html"
    redirect_authenticated_user = True


class PainelLogoutView(LogoutView):
    pass


@login_required
def painel_home(request):
    return render(request, "contas/painel_home.html")


class GestorRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return self.request.user.perfil == Usuario.Perfil.GESTOR


class CorretorRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return self.request.user.perfil == Usuario.Perfil.CORRETOR


class CorretorListView(GestorRequiredMixin, ListView):
    template_name = "contas/corretor_list.html"
    context_object_name = "corretores"

    def get_queryset(self):
        return Usuario.objects.filter(
            empresa=self.request.user.empresa, perfil=Usuario.Perfil.CORRETOR
        )


class CorretorCreateView(GestorRequiredMixin, CreateView):
    form_class = CorretorForm
    template_name = "contas/corretor_form.html"
    success_url = reverse_lazy("contas:corretor_list")

    def form_valid(self, form):
        corretor = form.save(commit=False)
        corretor.perfil = Usuario.Perfil.CORRETOR
        corretor.empresa = self.request.user.empresa
        corretor.full_clean(exclude=["password"])
        corretor.save()
        messages.success(self.request, "Corretor cadastrado com sucesso.")
        return redirect(self.success_url)


@login_required
@require_POST
def corretor_toggle_ativo(request, pk):
    if request.user.perfil != Usuario.Perfil.GESTOR:
        return redirect("contas:painel")

    corretor = get_object_or_404(
        Usuario, pk=pk, empresa=request.user.empresa, perfil=Usuario.Perfil.CORRETOR
    )
    corretor.is_active = not corretor.is_active
    corretor.save(update_fields=["is_active"])
    messages.success(
        request,
        "Corretor ativado." if corretor.is_active else "Corretor desativado.",
    )
    return redirect("contas:corretor_list")
