from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from proprietarios.models import Proprietario


class ProprietarioSerializer(serializers.ModelSerializer):
    documento_formatado = serializers.CharField(read_only=True)

    class Meta:
        model = Proprietario
        fields = [
            "id", "tipo_pessoa", "nome_razao_social", "documento", "documento_formatado",
            "telefone", "email", "observacoes", "criado_em",
        ]
        read_only_fields = ["id", "criado_em"]

    def validate(self, attrs):
        request = self.context["request"]
        instancia = self.instance or Proprietario()
        for campo, valor in attrs.items():
            setattr(instancia, campo, valor)
        instancia.empresa = request.user.empresa

        try:
            instancia.full_clean()
        except DjangoValidationError as erro:
            raise serializers.ValidationError(erro.message_dict)

        attrs["documento"] = instancia.documento  # normalizado (só dígitos) pelo clean()
        return attrs
