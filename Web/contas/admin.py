from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .forms import UsuarioChangeForm, UsuarioCreationForm
from .models import Usuario


@admin.register(Usuario)
class UsuarioAdmin(UserAdmin):
    add_form = UsuarioCreationForm
    form = UsuarioChangeForm
    model = Usuario

    list_display = ("email", "nome", "perfil", "empresa", "is_active")
    list_filter = ("perfil", "is_active", "empresa")
    search_fields = ("email", "nome")
    ordering = ("nome",)

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Dados pessoais", {"fields": ("nome", "perfil", "empresa")}),
        (
            "Perfil público do corretor",
            {"fields": ("creci", "foto", "telefone", "whatsapp", "apresentacao")},
        ),
        ("Permissões", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
    )
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("email", "nome", "perfil", "empresa", "password1", "password2"),
        }),
    )
    filter_horizontal = ("groups", "user_permissions")
