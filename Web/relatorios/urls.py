from django.urls import path

from . import views

app_name = "relatorios"

urlpatterns = [
    path("", views.RelatoriosIndexView.as_view(), name="index"),
    path("acervo/", views.AcervoView.as_view(), name="acervo"),
    path("contatos/", views.ContatosView.as_view(), name="contatos"),
    path("parcelas/", views.ParcelasView.as_view(), name="parcelas"),
]
