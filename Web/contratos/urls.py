from django.urls import path

from . import views

app_name = "contratos"

urlpatterns = [
    path("", views.ContratoListView.as_view(), name="contrato_list"),
    path("novo/", views.ContratoFormView.as_view(), name="contrato_create"),
    path("<int:pk>/", views.ContratoDetailView.as_view(), name="contrato_detail"),
    path("<int:pk>/editar/", views.ContratoFormView.as_view(), name="contrato_update"),
    path("<int:pk>/assinar/", views.contrato_assinar, name="contrato_assinar"),
]
