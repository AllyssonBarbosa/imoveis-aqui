from django.urls import path

from .views import (
    AcervoApiView, AcervoPorCorretorApiView, ContatosApiView, MudancasSituacaoApiView, ParcelasApiView,
)

urlpatterns = [
    path("relatorios/acervo/", AcervoApiView.as_view(), name="relatorio-acervo"),
    path("relatorios/acervo-por-corretor/", AcervoPorCorretorApiView.as_view(), name="relatorio-acervo-por-corretor"),
    path("relatorios/contatos/", ContatosApiView.as_view(), name="relatorio-contatos"),
    path("relatorios/mudancas-situacao/", MudancasSituacaoApiView.as_view(), name="relatorio-mudancas-situacao"),
    path("relatorios/parcelas/", ParcelasApiView.as_view(), name="relatorio-parcelas"),
]
