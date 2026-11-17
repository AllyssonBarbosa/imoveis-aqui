from django.urls import path

from . import views

app_name = "contratos"

urlpatterns = [
    path("", views.ContratoListView.as_view(), name="contrato_list"),
    path("novo/", views.ContratoFormView.as_view(), name="contrato_create"),
    path("<int:pk>/", views.ContratoDetailView.as_view(), name="contrato_detail"),
    path("<int:pk>/editar/", views.ContratoFormView.as_view(), name="contrato_update"),
    path("<int:pk>/assinar/", views.contrato_assinar, name="contrato_assinar"),
    path("<int:pk>/documentos/", views.documento_enviar, name="documento_enviar"),
    path(
        "<int:pk>/documentos/<int:documento_id>/baixar/", views.documento_baixar, name="documento_baixar"
    ),
    path(
        "<int:pk>/documentos/<int:documento_id>/excluir/", views.documento_excluir, name="documento_excluir"
    ),
    path("<int:pk>/parcelas/<int:parcela_id>/", views.ParcelaDetailView.as_view(), name="parcela_detail"),
    path(
        "<int:pk>/parcelas/<int:parcela_id>/baixas/<int:baixa_id>/comprovante/",
        views.comprovante_baixar,
        name="comprovante_baixar",
    ),
]
