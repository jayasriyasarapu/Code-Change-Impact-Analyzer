from rest_framework import generics, status, permissions
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from drf_spectacular.utils import extend_schema, OpenApiResponse
from .serializers import (
    UserRegisterSerializer,
    UserProfileSerializer,
    LogoutSerializer,
    UserSummarySerializer,
)


class RegisterView(generics.CreateAPIView):
    serializer_class = UserRegisterSerializer
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    @extend_schema(
        summary="Register a new user account",
        description="Creates a new user and returns user info along with initial JWT access and refresh tokens.",
        responses={
            201: OpenApiResponse(description="User successfully registered with JWT tokens"),
            400: OpenApiResponse(description="Validation error"),
        },
    )
    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        # Generate JWT tokens for instant login upon registration
        refresh = RefreshToken.for_user(user)
        user_data = UserSummarySerializer(user).data

        return Response(
            {
                "message": "User registered successfully.",
                "user": user_data,
                "tokens": {
                    "access": str(refresh.access_token),
                    "refresh": str(refresh),
                },
            },
            status=status.HTTP_201_CREATED,
        )


class ProfileView(generics.RetrieveUpdateAPIView):
    serializer_class = UserProfileSerializer
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(summary="Get current user profile")
    def get_object(self):
        return self.request.user


class LogoutView(generics.GenericAPIView):
    serializer_class = LogoutSerializer
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        summary="Logout user and invalidate refresh token",
        description="Blacklists the provided refresh token so it cannot be used again.",
        responses={
            200: OpenApiResponse(description="Token blacklisted successfully"),
            400: OpenApiResponse(description="Invalid or expired token"),
        },
    )
    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({"message": "Successfully logged out."}, status=status.HTTP_200_OK)
