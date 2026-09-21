from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from core.models import Endereco

from ..models import Caracteristica, FotoImovel, Imovel


class CaracteristicaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Caracteristica
        fields = ["id", "nome"]


class EnderecoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Endereco
        fields = [
            "id", "cep", "logradouro", "numero", "complemento", "bairro",
            "cidade", "latitude", "longitude",
        ]


class FotoImovelSerializer(serializers.ModelSerializer):
    class Meta:
        model = FotoImovel
        fields = ["id", "imovel", "imagem", "ordem", "capa"]
        read_only_fields = ["id", "imovel", "capa"]


class ImovelSerializer(serializers.ModelSerializer):
    endereco = EnderecoSerializer(read_only=True)
    fotos = FotoImovelSerializer(many=True, read_only=True)
    pode_publicar = serializers.BooleanField(read_only=True)

    class Meta:
        model = Imovel
        fields = [
            "id", "codigo", "titulo", "corretor_responsavel", "proprietario", "finalidade",
            "preco_venda", "preco_aluguel", "preco_condominio", "preco_iptu", "descricao",
            "caracteristicas", "endereco", "fotos", "publicado", "pode_publicar", "criado_em",
        ]
        read_only_fields = ["id", "publicado", "endereco", "fotos", "pode_publicar", "criado_em"]

    def validate(self, attrs):
        request = self.context["request"]
        instancia = self.instance or Imovel()
        for campo, valor in attrs.items():
            if campo == "caracteristicas":
                continue
            setattr(instancia, campo, valor)
        instancia.empresa = request.user.empresa

        try:
            instancia.full_clean(exclude=["endereco"])
        except DjangoValidationError as erro:
            raise serializers.ValidationError(erro.message_dict)
        return attrs

    def create(self, validated_data):
        caracteristicas = validated_data.pop("caracteristicas", [])
        imovel = Imovel.objects.create(**validated_data)
        imovel.caracteristicas.set(caracteristicas)
        return imovel

    def update(self, instance, validated_data):
        caracteristicas = validated_data.pop("caracteristicas", None)
        for campo, valor in validated_data.items():
            setattr(instance, campo, valor)
        instance.save()
        if caracteristicas is not None:
            instance.caracteristicas.set(caracteristicas)
        return instance
