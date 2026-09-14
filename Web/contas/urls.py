from django.urls import path

from . import views

app_name = "contas"

urlpatterns = [
    path("login/", views.PainelLoginView.as_view(), name="login"),
    path("logout/", views.PainelLogoutView.as_view(), name="logout"),
    path("", views.painel_home, name="painel"),
    path("corretores/", views.CorretorListView.as_view(), name="corretor_list"),
    path("corretores/novo/", views.CorretorCreateView.as_view(), name="corretor_create"),
    path(
        "corretores/<int:pk>/ativar-desativar/",
        views.corretor_toggle_ativo,
        name="corretor_toggle_ativo",
    ),
]
