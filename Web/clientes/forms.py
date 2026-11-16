from django import forms

from .models import Cliente


class ClienteForm(forms.ModelForm):
    class Meta:
        model = Cliente
        fields = [
            "tipo_pessoa", "nome_razao_social", "documento", "telefone", "email", "observacoes",
        ]
        widgets = {"observacoes": forms.Textarea(attrs={"rows": 3})}
