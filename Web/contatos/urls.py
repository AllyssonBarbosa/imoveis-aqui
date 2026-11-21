from django.urls import path

from . import views

app_name = "contatos"

urlpatterns = [
    path("", views.ContatoListView.as_view(), name="contato_list"),
    path("<int:pk>/retorno/", views.contato_anotar_retorno, name="contato_anotar_retorno"),
]
