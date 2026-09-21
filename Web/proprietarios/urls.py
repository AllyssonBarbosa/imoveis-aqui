from django.urls import path

from . import views

app_name = "proprietarios"

urlpatterns = [
    path("", views.ProprietarioListView.as_view(), name="proprietario_list"),
    path("novo/", views.ProprietarioCreateView.as_view(), name="proprietario_create"),
]
