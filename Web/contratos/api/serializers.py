from django.core.exceptions import NON_FIELD_ERRORS
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from rest_framework.settings import api_settings

from clientes.models import Cliente
from contas.models import Usuario
from imoveis.models import Imovel

from ..models import Contrato, DocumentoContrato, Parcela


def erros_para_api(erro):
    """ValidationError do Django → formato do DRF (__all__ vira non_field_errors)."""
    if hasattr(erro, "error_dict"):
        erros = dict(erro.message_dict)
        if NON_FIELD_ERRORS in erros:
            erros[api_settings.NON_FIELD_ERRORS_KEY] = erros.pop(NON_FIELD_ERRORS)
        return serializers.ValidationError(erros)
    return serializers.ValidationError({api_settings.NON_FIELD_ERRORS_KEY: erro.messages})


class ParcelaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Parcela
        fields = ["id", "numero", "vencimento", "valor"]
        read_only_fields = fields


class DocumentoContratoSerializer(serializers.ModelSerializer):
    """Documento do contrato. O arquivo entra no envio, mas nunca sai como URL:
    o download é pela rota `baixar`, que confere empresa e perfil."""

    arquivo = serializers.FileField(write_only=True, help_text="PDF, JPG ou PNG de até 10MB.")
    enviado_por = serializers.CharField(source="enviado_por.nome", read_only=True)
    url_download = serializers.SerializerMethodField()

    class Meta:
        model = DocumentoContrato
        fields = ["id", "contrato", "tipo", "descricao", "arquivo", "enviado_por", "enviado_em", "url_download"]
        read_only_fields = ["id", "enviado_por", "enviado_em"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        if request is not None and request.user.is_authenticated and "contrato" in self.fields:
            from ..servicos import contratos_visiveis_para

            # contrato de outra empresa cai em "não existe", sem revelar que existe
            self.fields["contrato"].queryset = contratos_visiveis_para(request.user)

    def get_url_download(self, documento):
        from django.urls import reverse

        url = reverse("documento-contrato-baixar", args=[documento.pk])
        request = self.context.get("request")
        return request.build_absolute_uri(url) if request else url

    def validate(self, attrs):
        contrato = attrs["contrato"]
        instancia = DocumentoContrato(
            empresa_id=contrato.empresa_id, enviado_por=self.context["request"].user, **attrs
        )
        try:
            instancia.full_clean()
        except DjangoValidationError as erro:
            raise erros_para_api(erro)
        return attrs


class ContratoSerializer(serializers.ModelSerializer):
    """Contrato com as parcelas (só leitura — quem gera é o servidor)."""

    situacao = serializers.CharField(read_only=True)
    imovel_codigo = serializers.CharField(source="imovel.codigo", read_only=True)
    cliente_nome = serializers.CharField(source="cliente.nome_razao_social", read_only=True)
    corretor_nome = serializers.CharField(source="corretor.nome", read_only=True)
    total_parcelado = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    parcelas = ParcelaSerializer(many=True, read_only=True)
    documentos = DocumentoContratoSerializer(many=True, read_only=True)

    class Meta:
        model = Contrato
        fields = [
            "id", "tipo", "situacao", "imovel", "imovel_codigo", "cliente", "cliente_nome",
            "corretor", "corretor_nome", "valor", "entrada", "numero_parcelas", "dia_vencimento",
            "data_inicio", "data_fim", "observacoes", "data_assinatura", "total_parcelado",
            "criado_em", "atualizado_em", "parcelas", "documentos",
        ]
        read_only_fields = ["id", "data_assinatura", "criado_em", "atualizado_em"]
        # entrada em branco/null = sem entrada (o caso de toda locação)
        extra_kwargs = {"entrada": {"required": False, "allow_null": True}}
        # a regra "um contrato assinado por imóvel" é checada no full_clean
        validators = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        if request is not None and request.user.is_authenticated:
            empresa = request.user.empresa_id
            # de outra empresa cai em "não existe", sem revelar que existe
            self.fields["imovel"].queryset = Imovel.objects.filter(empresa_id=empresa)
            self.fields["cliente"].queryset = Cliente.objects.filter(empresa_id=empresa)
            self.fields["corretor"].queryset = Usuario.objects.filter(
                empresa_id=empresa, perfil=Usuario.Perfil.CORRETOR
            )

    def _salvar(self, contrato, dados):
        from ..servicos import salvar_contrato

        for campo, valor in dados.items():
            setattr(contrato, campo, valor)
        if contrato.entrada is None:
            contrato.entrada = 0
        contrato.empresa = self.context["request"].user.empresa
        try:
            return salvar_contrato(contrato, self.context["request"].user)
        except DjangoValidationError as erro:
            raise erros_para_api(erro)

    def create(self, validated_data):
        return self._salvar(Contrato(), validated_data)

    def update(self, instance, validated_data):
        return self._salvar(instance, validated_data)


class AssinaturaSerializer(serializers.Serializer):
    data_assinatura = serializers.DateField()
