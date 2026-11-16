from django.urls import path

from . import views

app_name = "clientes"

urlpatterns = [
    path("", views.ClienteListView.as_view(), name="cliente_list"),
    path("novo/", views.ClienteCreateView.as_view(), name="cliente_create"),
    path("<int:pk>/editar/", views.ClienteUpdateView.as_view(), name="cliente_update"),
]
