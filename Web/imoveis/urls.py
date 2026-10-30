from django.urls import path

from . import views

app_name = "imoveis"

urlpatterns = [
    path("", views.ImovelListView.as_view(), name="imovel_list"),
    path("novo/", views.ImovelCreateView.as_view(), name="imovel_create"),
    path("<int:pk>/", views.ImovelDetailView.as_view(), name="imovel_detail"),
    path("<int:pk>/detalhes/", views.imovel_atualizar_detalhes, name="imovel_atualizar_detalhes"),
    path("<int:pk>/endereco/", views.imovel_atualizar_endereco, name="imovel_atualizar_endereco"),
    path("<int:pk>/fotos/", views.imovel_upload_fotos, name="imovel_upload_fotos"),
    path("<int:pk>/fotos/<int:foto_id>/capa/", views.imovel_definir_capa, name="imovel_definir_capa"),
    path("<int:pk>/fotos/<int:foto_id>/excluir/", views.imovel_excluir_foto, name="imovel_excluir_foto"),
    path("gestao/", views.GestaoImovelListView.as_view(), name="gestao_imovel_list"),
    path("gestao/<int:pk>/", views.gestao_imovel_detail, name="gestao_imovel_detail"),
    path("gestao/<int:pk>/publicar/", views.gestao_imovel_publicar, name="gestao_imovel_publicar"),
    path("gestao/<int:pk>/despublicar/", views.gestao_imovel_despublicar, name="gestao_imovel_despublicar"),
    path("gestao/<int:pk>/situacao/", views.gestao_imovel_situacao, name="gestao_imovel_situacao"),
    path("gestao/<int:pk>/documentos/", views.gestao_documento_enviar, name="gestao_documento_enviar"),
    path(
        "gestao/<int:pk>/documentos/<int:documento_id>/",
        views.gestao_documento_baixar, name="gestao_documento_baixar",
    ),
    path(
        "gestao/<int:pk>/documentos/<int:documento_id>/excluir/",
        views.gestao_documento_excluir, name="gestao_documento_excluir",
    ),
    path("predios/", views.PredioListView.as_view(), name="predio_list"),
    path("predios/novo/", views.predio_create, name="predio_create"),
    path("predios/<int:pk>/", views.predio_update, name="predio_update"),
    path("loteamentos/", views.LoteamentoListView.as_view(), name="loteamento_list"),
    path("loteamentos/novo/", views.loteamento_create, name="loteamento_create"),
    path("loteamentos/<int:pk>/", views.loteamento_detail, name="loteamento_detail"),
    path("loteamentos/<int:pk>/editar/", views.loteamento_update, name="loteamento_update"),
]
