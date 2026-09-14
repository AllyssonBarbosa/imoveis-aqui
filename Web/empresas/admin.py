from django.contrib import admin

from .models import Empresa


@admin.register(Empresa)
class EmpresaAdmin(admin.ModelAdmin):
    list_display = ("razao_social_ou_nome", "tipo", "cnpj", "cpf", "creci", "ativa")
    list_filter = ("tipo", "ativa", "cidades_atuacao")
    search_fields = ("razao_social_ou_nome", "cnpj", "cpf", "creci")
    filter_horizontal = ("cidades_atuacao",)
    fieldsets = (
        (None, {"fields": ("tipo", "razao_social_ou_nome", "ativa")}),
        ("Documentos", {"fields": ("cnpj", "cpf", "creci")}),
        ("Contato", {"fields": ("telefone", "whatsapp", "email")}),
        ("Apresentação", {"fields": ("logomarca", "descricao")}),
        ("Atuação", {"fields": ("endereco", "cidades_atuacao")}),
    )
