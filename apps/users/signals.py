from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Profile
from apps.outsourcing.models import Task, Application

@receiver(post_save, sender=Task)
def update_user_task_stats(sender, instance, created, **kwargs):
    if created:
        profile = instance.creator.profile
        profile.total_tasks_posted += 1
        profile.save()

@receiver(post_save, sender=Application)
def update_user_stats_on_application(sender, instance, **kwargs):
    if instance.status == 'accepted':
        # Update applicant's profile
        applicant_profile = instance.applicant.profile
        applicant_profile.total_tasks_completed += 1
        applicant_profile.total_earned += instance.task.budget or 0
        applicant_profile.save()
        
        # Update creator's profile
        creator_profile = instance.task.creator.profile
        creator_profile.total_tasks_completed += 1
        creator_profile.save()