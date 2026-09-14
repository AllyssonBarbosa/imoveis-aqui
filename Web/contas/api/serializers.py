from django.contrib.auth import authenticate
from rest_framework import serializers

from contas.models import Usuario


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate(self, attrs):
        usuario = authenticate(
            request=self.context.get("request"),
            username=attrs["email"],
            password=attrs["password"],
        )
        if usuario is None:
            raise serializers.ValidationError(
                "E-mail ou senha inválidos.", code="authorization"
            )
        attrs["usuario"] = usuario
        return attrs


class CorretorPublicoSerializer(serializers.Serializer):
    """Perfil público do corretor — usado pela vitrine (site e app), sem login.

    Corretor autônomo não duplica cadastro: o que ele não preencheu vem
    da própria Empresa (ver Usuario.dados_perfil_publico).
    """

    def to_representation(self, instance):
        dados = instance.dados_perfil_publico()
        foto = dados.pop("foto")
        request = self.context.get("request")
        foto_url = None
        if foto:
            foto_url = request.build_absolute_uri(foto.url) if request else foto.url
        return {
            "id": instance.pk,
            "empresa_id": instance.empresa_id,
            "foto": foto_url,
            **dados,
        }


class UsuarioSerializer(serializers.ModelSerializer):
    perfil_exibicao = serializers.CharField(source="get_perfil_display", read_only=True)

    class Meta:
        model = Usuario
        fields = ["id", "nome", "email", "perfil", "perfil_exibicao", "empresa", "is_active"]
        read_only_fields = ["id", "empresa"]
