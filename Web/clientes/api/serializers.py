from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from clientes.models import Cliente


class ClienteSerializer(serializers.ModelSerializer):
    """API interna (gestor/corretor logado). Este serializer nunca é usado na vitrine."""

    documento_formatado = serializers.CharField(read_only=True)

    class Meta:
        model = Cliente
        fields = [
            "id", "tipo_pessoa", "nome_razao_social", "documento", "documento_formatado",
            "telefone", "email", "observacoes", "criado_em", "atualizado_em",
        ]
        read_only_fields = ["id", "criado_em", "atualizado_em"]
        # a unicidade (empresa, documento) é checada no full_clean, com a mensagem em português
        validators = []

    def validate(self, attrs):
        request = self.context["request"]
        instancia = self.instance or Cliente()
        for campo, valor in attrs.items():
            setattr(instancia, campo, valor)
        instancia.empresa = request.user.empresa

        try:
            instancia.full_clean()
        except DjangoValidationError as erro:
            raise serializers.ValidationError(erro.message_dict)

        attrs["documento"] = instancia.documento  # normalizado (só dígitos) pelo clean()
        return attrs
