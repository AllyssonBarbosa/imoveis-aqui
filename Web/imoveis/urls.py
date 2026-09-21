from django.urls import path

from . import views

app_name = "imoveis"

urlpatterns = [
    path("", views.ImovelListView.as_view(), name="imovel_list"),
    path("novo/", views.ImovelCreateView.as_view(), name="imovel_create"),
    path("<int:pk>/", views.ImovelDetailView.as_view(), name="imovel_detail"),
    path("<int:pk>/endereco/", views.imovel_atualizar_endereco, name="imovel_atualizar_endereco"),
    path("<int:pk>/fotos/", views.imovel_upload_fotos, name="imovel_upload_fotos"),
    path("<int:pk>/fotos/<int:foto_id>/capa/", views.imovel_definir_capa, name="imovel_definir_capa"),
    path("<int:pk>/fotos/<int:foto_id>/excluir/", views.imovel_excluir_foto, name="imovel_excluir_foto"),
    path("<int:pk>/publicar/", views.imovel_publicar, name="imovel_publicar"),
]
