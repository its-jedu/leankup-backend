from django.urls import path
from . import views

urlpatterns = [
    path('me/', views.ProfileDetailView.as_view(), name='profile-detail'),
    path('profile/<int:user_id>/', views.UserProfileView.as_view(), name='user-profile'),
]