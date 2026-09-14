from rest_framework import generics, viewsets
from rest_framework.authtoken.models import Token
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.mixins import EmpresaScopedQuerySetMixin
from core.permissions import PertenceAUmaEmpresa

from contas.models import Usuario

from .serializers import CorretorPublicoSerializer, LoginSerializer, UsuarioSerializer


class LoginAPIView(APIView):
    """Devolve um token válido para o usuário autenticado (gestor ou corretor)."""

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        usuario = serializer.validated_data["usuario"]
        token, _ = Token.objects.get_or_create(user=usuario)
        return Response({"token": token.key})


class UsuarioViewSet(EmpresaScopedQuerySetMixin, viewsets.ReadOnlyModelViewSet):
    """Lista os usuários (corretores) da própria empresa — base do isolamento multitenant."""

    serializer_class = UsuarioSerializer
    permission_classes = [PertenceAUmaEmpresa]
    queryset = Usuario.objects.all()


class CorretorPublicoAPIView(generics.RetrieveAPIView):
    """Perfil público do corretor, sem login. Corretor desativado não aparece."""

    permission_classes = [AllowAny]
    serializer_class = CorretorPublicoSerializer
    queryset = Usuario.objects.filter(perfil=Usuario.Perfil.CORRETOR, is_active=True)
