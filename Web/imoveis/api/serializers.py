from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from core.models import Endereco

from ..models import Caracteristica, Casa, FotoImovel, Imovel, Terreno


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


class DetalhesSerializerBase(serializers.ModelSerializer):
    """Valida pelo full_clean do model — a regra fica num lugar só (models.py).

    Espera o imóvel em context["imovel"]; ele não é campo editável.
    """

    def validate(self, attrs):
        instancia = self.instance or self.Meta.model(imovel=self.context["imovel"])
        for campo, valor in attrs.items():
            setattr(instancia, campo, valor)
        try:
            instancia.full_clean(exclude=["imovel"])
        except DjangoValidationError as erro:
            raise serializers.ValidationError(erro.message_dict)
        return attrs

    def create(self, validated_data):
        return self.Meta.model.objects.create(imovel=self.context["imovel"], **validated_data)


class CasaSerializer(DetalhesSerializerBase):
    area_livre = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)

    class Meta:
        model = Casa
        fields = [
            "area_terreno", "area_construida", "area_livre", "quartos", "suites", "banheiros",
            "salas", "vagas", "pavimentos", "ano_construcao", "piscina", "quintal",
            "churrasqueira", "mobiliada",
        ]


class TerrenoSerializer(DetalhesSerializerBase):
    class Meta:
        model = Terreno
        fields = [
            "area_total", "frente", "fundo", "lateral_esquerda", "lateral_direita",
            "topografia", "murado", "esquina", "uso_permitido",
        ]


SERIALIZER_POR_NATUREZA = {
    Imovel.Natureza.CASA: CasaSerializer,
    Imovel.Natureza.TERRENO: TerrenoSerializer,
}


class ImovelSerializer(serializers.ModelSerializer):
    endereco = EnderecoSerializer(read_only=True)
    casa = serializers.SerializerMethodField()
    terreno = serializers.SerializerMethodField()
    fotos = FotoImovelSerializer(many=True, read_only=True)
    pode_publicar = serializers.BooleanField(read_only=True)

    class Meta:
        model = Imovel
        fields = [
            "id", "codigo", "titulo", "natureza", "corretor_responsavel", "proprietario", "finalidade",
            "preco_venda", "preco_aluguel", "preco_condominio", "preco_iptu", "descricao",
            "caracteristicas", "casa", "terreno", "endereco", "fotos", "publicado", "pode_publicar", "criado_em",
        ]
        read_only_fields = ["id", "publicado", "endereco", "fotos", "pode_publicar", "criado_em"]

    def _detalhes_se_for(self, imovel, natureza):
        if imovel.natureza != natureza or imovel.detalhes is None:
            return None
        return SERIALIZER_POR_NATUREZA[natureza](imovel.detalhes).data

    def get_casa(self, imovel):
        return self._detalhes_se_for(imovel, Imovel.Natureza.CASA)

    def get_terreno(self, imovel):
        return self._detalhes_se_for(imovel, Imovel.Natureza.TERRENO)

    def validate_natureza(self, valor):
        if self.instance and valor != self.instance.natureza:
            raise serializers.ValidationError("A natureza não pode ser trocada depois do cadastro.")
        return valor

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
