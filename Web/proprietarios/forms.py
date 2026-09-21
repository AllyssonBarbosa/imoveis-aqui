from django import forms

from .models import Proprietario


class ProprietarioForm(forms.ModelForm):
    class Meta:
        model = Proprietario
        fields = [
            "tipo_pessoa", "nome_razao_social", "documento", "telefone", "email", "observacoes",
        ]
