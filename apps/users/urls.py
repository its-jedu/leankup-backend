from django.urls import path
from . import views

urlpatterns = [
    path('me/', views.ProfileDetailView.as_view(), name='profile-detail'),
]