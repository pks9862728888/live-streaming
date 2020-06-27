from django.urls import path

from . import views

app_name = 'institute'

urlpatterns = [
    path('institute-min-details-teacher-admin',
         views.InstituteMinDetailsTeacherView.as_view(),
         name="institute-min-details-teacher-admin"),
    path('create-institute',
         views.CreateInstituteView.as_view(),
         name="create-institute"),
    path('detail/<slug:institute_slug>',
         views.InstituteFullDetailsView.as_view(),
         name="detail")
]
