from django.urls import path

from .views import AcervoApiView, ContatosApiView, ParcelasApiView

urlpatterns = [
    path("relatorios/acervo/", AcervoApiView.as_view(), name="relatorio-acervo"),
    path("relatorios/contatos/", ContatosApiView.as_view(), name="relatorio-contatos"),
    path("relatorios/parcelas/", ParcelasApiView.as_view(), name="relatorio-parcelas"),
]
