from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from rest_framework import permissions

# Only import drf_yasg if DEBUG is True
if settings.DEBUG:
    try:
        from drf_yasg.views import get_schema_view
        from drf_yasg import openapi
        
        schema_view = get_schema_view(
            openapi.Info(
                title="LeankUp API",
                default_version='v1',
                description="API for LeankUp - Local Outsourcing & Fundraising Platform",
                terms_of_service="https://www.leankup.com/terms/",
                contact=openapi.Contact(email="support@leankup.com"),
                license=openapi.License(name="BSD License"),
            ),
            public=True,
            permission_classes=[permissions.AllowAny],
        )
        SWAGGER_AVAILABLE = True
    except ImportError:
        SWAGGER_AVAILABLE = False
else:
    SWAGGER_AVAILABLE = False

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/auth/', include('apps.auth.urls')),
    path('api/users/', include('apps.users.urls')),
    path('api/tasks/', include('apps.outsourcing.urls')),
    path('api/campaigns/', include('apps.fundraising.urls')),
    path('api/wallet/', include('apps.wallet.urls')),
    path('api/payments/', include('apps.payments.urls')),
]

# Only add swagger URLs if available
if SWAGGER_AVAILABLE:
    urlpatterns += [
        path('swagger/', schema_view.with_ui('swagger', cache_timeout=0), name='schema-swagger-ui'),
        path('redoc/', schema_view.with_ui('redoc', cache_timeout=0), name='schema-redoc'),
    ]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
