import os
import datetime
from decimal import Decimal
import time
from math import ceil

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.db.models import Max, Q, F
from django.utils import timezone
from django.utils.translation import ugettext as _
from django.shortcuts import get_object_or_404

from rest_framework.authentication import TokenAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework import permissions, status
from rest_framework.views import APIView
from rest_framework.generics import ListAPIView, CreateAPIView,\
    RetrieveAPIView, DestroyAPIView
from rest_framework.response import Response
from rest_framework.parsers import JSONParser, MultiPartParser

import filetype
from razorpay.errors import SignatureVerificationError
from PIL import Image

import ffmpeg_streaming
from ffmpeg_streaming import Formats, Bitrate, Representation, Size, FFProbe

from . import serializer
from app.settings import client, MEDIA_URL, MEDIA_ROOT
from core import models


def get_active_or_expired_common_license(institute):
    """Returns common license order if order is active or expired"""
    order = models.InstituteCommonLicenseOrderDetails.objects.filter(
        institute=institute,
        paid=True
    ).order_by('-order_created_on').first()

    if not order:
        return None
    else:
        return order


def get_active_common_license(institute):
    """Returns license order if institute has active license else returns None"""
    order = models.InstituteCommonLicenseOrderDetails.objects.filter(
        institute=institute,
        paid=True,
        active=True
    ).order_by('order_created_on').first()

    if not order or (int(time.time()) * 1000 > order.end_date):
        return None
    else:
        return order


def get_institute_stats_and_validate(institute, size=None):
    """
    Returns institute statistics if validation success else return error response
    """
    stats = models.InstituteStatistics.objects.filter(institute=institute).first()
    order = get_active_common_license(institute)
    if not order:
        return Response({'error': _('License not found.')},
                        status=status.HTTP_400_BAD_REQUEST)
    elif size:
        if stats.storage > order.selected_license.storage:
            return Response({'error': _('Maximum storage limit reached. To get more storage contact us.')},
                            status=status.HTTP_400_BAD_REQUEST)
        elif stats.storage + size > order.selected_license.storage:
            return Response({'error': _('File size too large. Allowed storage limit will get exceeded. To get more storage contact us.')},
                            status=status.HTTP_400_BAD_REQUEST)
    return stats


def monitor(ffmpeg, duration, time_, time_left, process):
    """Realtime information about ffmpeg transcoding process"""
    per = round(time_ / duration * 100)
    print('*' * per + str(per) + '% completed')


def get_file_lecture_material_data(data, data_notation, base_url, size=None):
    """
    Creates and returns image content data dict,
    size is in GB
    """
    if data_notation == 'SER':
        return {
            'id': data['id'],
            'file': str(data['file']),
            'can_download': data['can_download']
        }
    elif data_notation == 'OBJ':
        if data:
            return {
                'id': data.pk,
                'file': base_url + str(data.file),
                'can_download': data.can_download
            }
        else:
            return {}


def get_study_material_content_details(data, data_notation):
    """
    Creates and returns study material data.
    """
    if data_notation == 'OBJ':
        response = dict()
        response['id'] = data.pk
        response['title'] = data.title
        response['order'] = data.order
        response['content_type'] = data.content_type
        response['view'] = data.view.key
        response['uploaded_on'] = str(data.uploaded_on)

        if data.week:
            response['week'] = data.week.value

        if data.description:
            response['description'] = data.description

        if data.target_date:
            response['target_date'] = str(data.target_date)

        return response


def get_external_link_study_material_data(data, data_notation):
    """
    Creates and returns external link study material data.
    """
    if data_notation == 'SER':
        return {
            'id': data['id'],
            'url': data['url']
        }
    elif data_notation == 'OBJ':
        if data:
            return {
                'id': data.pk,
                'url': data.url
            }
        else:
            return {}


def get_image_study_material_data(data, data_notation, base_url, size=None):
    """
    Creates and returns image content data dict,
    size is in GB
    """
    if data_notation == 'SER':
        return {
            'id': data['id'],
            'file': str(data['file']),
            'size': size,
            'can_download': data['can_download']
        }
    elif data_notation == 'OBJ':
        if data:
            return {
                'id': data.pk,
                'file': base_url + str(data.file),
                'size': float(data.file.size) / 1000000000,
                'can_download': data.can_download
            }
        else:
            return {}


def get_video_study_material_data(data, data_notation, base_url):
    """
    Creates and returns video study material data,
    size is in GB
    """
    if data:
        response = dict()
        if data_notation == 'OBJ':
            response['id'] = data.id
            response['size'] = float(data.file.size) / 1000000000
            response['can_download'] = data.can_download
            response['bit_rate'] = data.bit_rate
            response['error_transcoding'] = data.error_transcoding

            if data.duration:
                response['duration'] = data.duration

            if data.stream_file:
                response['stream_file'] = base_url + str(data.stream_file)

            if data.can_download or data.error_transcoding:
                response['file'] = base_url + str(data.file)

        return response
    else:
        return {}


def get_pdf_study_material_data(data, data_notation, base_url, size=None):
    """
    Creates and returns pdf content data dict,
    size is in GB
    """
    if data:
        if data_notation == 'SER':
            return {
                'id': data['id'],
                'file': str(data['file']),
                'size': size,
                'can_download': data['can_download']
            }
        elif data_notation == 'OBJ':
            res = {
                'id': data.pk,
                'file': base_url + str(data.file),
                'size': float(data.file.size) / 1000000000,
                'can_download': data.can_download
            }
            if data.duration:
                res['duration'] = data.duration
            return res

    else:
        return {}


def validate_image_file(file):
    """Checking whether the file is an image file"""
    try:
        if not file:
            return Response({'error': _('File is required.')},
                            status=status.HTTP_400_BAD_REQUEST)
        Image.open(file).verify()
    except Exception:
        return Response({'error': _(
            'Upload a valid image. The file you uploaded was either not an image or a corrupted image.')},
                        status=status.HTTP_400_BAD_REQUEST)
    return None


def validate_pdf_file(file, raw_file):
    """Checking whether the file is pdf file"""
    try:
        if not file:
            return Response({'error': _('File is required.')},
                            status=status.HTTP_400_BAD_REQUEST)
        elif not filetype.is_archive(file):
            return Response({'error': _('Not a valid pdf file.')},
                            status=status.HTTP_400_BAD_REQUEST)
        else:
            kind = os.path.splitext(raw_file)[1]
            if not kind.endswith('.pdf'):
                return Response({'error': _('Not a valid pdf file.')},
                                status=status.HTTP_400_BAD_REQUEST)
    except Exception:
        return Response({'error': _('An internal error occurred')},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    return None


class IsTeacher(permissions.BasePermission):
    """Permission that allows only teacher to access this view"""

    def has_permission(self, request, view):
        """
        Return `True` if teacher is user, `False` otherwise.
        """
        if request.user and request.user.is_teacher:
            return True
        else:
            return False


class IsTeacherOrStudent(permissions.BasePermission):
    """Permission that allows only teacher or student to view"""

    def has_permission(self, request, view):
        if request.user and (request.user.is_teacher or request.user.is_student):
            return True
        else:
            return False


class IsStudent(permissions.BasePermission):
    """Permission that allows only student to view"""

    def has_permission(self, request, view):
        if request.user and request.user.is_student:
            return True
        else:
            return False


class GetInstituteDiscountCouponView(APIView):
    """View to get institute discount coupon"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def post(self, request, *args, **kwargs):
        """Used for getting discount coupon details"""
        if not models.InstitutePermission.objects.filter(
            invitee=self.request.user.pk,
            role=models.InstituteRole.ADMIN
        ).exists():
            return Response({'error': _('Invalid permission')},
                            status=status.HTTP_400_BAD_REQUEST)

        coupon_code = request.data.get('coupon_code')

        if not coupon_code:
            return Response({'error': _('Coupon code is required.')},
                            status=status.HTTP_400_BAD_REQUEST)

        coupon = models.InstituteDiscountCoupon.objects.filter(
            coupon_code=coupon_code
        ).first()

        if not coupon:
            return Response({'error': _('Coupon not found.')},
                            status=status.HTTP_400_BAD_REQUEST)
        if not coupon.active:
            return Response({'error': _('Coupon already used.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if int(time.time()) * 1000 > coupon.expiry_date:
            return Response({'error': _('Coupon expired.')},
                            status=status.HTTP_400_BAD_REQUEST)

        return Response({'discount_rs': coupon.discount_rs,
                         'active': coupon.active},
                        status=status.HTTP_200_OK)


class InstituteLicenseCostView(ListAPIView):
    """
    View for getting cost of institute storage license
    """
    authentication_classes = (TokenAuthentication, )
    permission_classes = (IsAuthenticated, IsTeacher)

    def get(self, *args, **kwargs):
        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug')
        ).only('institute_slug').first()

        if not institute:
            return Response({'error': _('Institute not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        institute_stat = models.InstituteStatistics.objects.filter(
            institute=institute
        ).only('storage').first()

        license_stat = models.InstituteLicenseStat.objects.filter(
            institute=institute
        ).only('total_storage').first()

        min_storage = max(20, int(abs(ceil(float(license_stat.total_storage - institute_stat.storage)))))

        lic = models.InstituteStorageLicense.objects.all().first()

        if not lic:
            return Response({'error': _('This product is currently not available.')},
                            status=status.HTTP_400_BAD_REQUEST)

        return Response({
            'price': lic.price,
            'gst_percent': lic.gst_percent,
            'min_storage': min_storage
        }, status=status.HTTP_200_OK)


class InstituteCreateStorageLicenseOrderView(APIView):
    """View for creating storage license order"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def post(self, request, *args, **kwargs):
        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug')
        ).only('institute_slug').first()

        if not institute:
            return Response({'error': _('Institute not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstitutePermission.objects.filter(
                institute=institute,
                invitee=self.request.user,
                active=True,
                role=models.InstituteRole.ADMIN
        ):
            return Response({'error': _('Permission denied [Admin only]')},
                            status=status.HTTP_400_BAD_REQUEST)

        contact = ''
        lic = models.InstituteStorageLicense.objects.all().first()

        try:
            contact = str(self.request.user.user_profile.phone)
        except Exception:
            pass

        try:
            prev_order = models.InstituteStorageLicenseOrderDetails.objects.filter(
                institute=institute,
                paid=False,
                active=False,
                no_of_gb=request.data.get('no_of_gb'),
                months=request.data.get('months')
            ).first()

            if prev_order:
                if prev_order.payment_gateway != request.data.get('payment_gateway'):
                    prev_order.payment_gateway = request.data.get('payment_gateway')
                    # Generate order with new payment gateway
                    prev_order.save()

                if prev_order.payment_gateway == models.PaymentGateway.RAZORPAY:
                    return Response(
                        {'status': 'SUCCESS',
                         'amount': prev_order.amount,
                         'key_id': os.environ.get('RAZORPAY_TEST_KEY_ID'),
                         'currency': prev_order.currency,
                         'order_id': prev_order.order_id,
                         'order_details_id': prev_order.pk,
                         'email': str(self.request.user),
                         'contact': contact,
                         'no_of_gb': prev_order.no_of_gb,
                         'months': prev_order.months
                         }, status=status.HTTP_201_CREATED)
                else:
                    pass  # Generate appropriate response

            order = models.InstituteStorageLicenseOrderDetails.objects.create(
                institute=institute,
                payment_gateway=request.data.get('payment_gateway'),
                price=lic.price,
                gst_percent=lic.gst_percent,
                no_of_gb=request.data.get('no_of_gb'),
                months=request.data.get('months')
            )

            if order.payment_gateway == models.PaymentGateway.RAZORPAY:
                return Response(
                    {'status': 'SUCCESS',
                     'amount': order.amount,
                     'key_id': os.environ.get('RAZORPAY_TEST_KEY_ID'),
                     'currency': order.currency,
                     'order_id': order.order_id,
                     'order_details_id': order.pk,
                     'email': str(self.request.user),
                     'contact': contact,
                     'no_of_gb': order.no_of_gb,
                     'months': order.months},
                    status=status.HTTP_201_CREATED)
            else:
                pass  # Generate response
        except Exception as e:
            print(e)
            return Response({'error': _('Internal server error.')},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class RazorpayStoragePaymentCallbackView(APIView):
    """
    View for storing storage payment callback data
    and checking whether payment was successful
    """
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def post(self, request, *args, **kwargs):
        params_dict = {
            'razorpay_order_id': request.data.get('razorpay_order_id'),
            'razorpay_payment_id': request.data.get('razorpay_payment_id'),
            'razorpay_signature': request.data.get('razorpay_signature')
        }
        order_details_id = request.data.get('order_details_id')

        if not params_dict['razorpay_order_id'] or\
                not params_dict['razorpay_payment_id'] or\
                not params_dict['razorpay_signature'] or\
                not order_details_id:
            return Response({'error': _('Invalid fields. Contact support.')},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            order = models.InstituteStorageLicenseOrderDetails.objects.filter(
                pk=order_details_id
            ).first()

            if not order:
                return Response({
                    'error': _('Order not found. If payment is successful it will be verified automatically.')},
                                status=status.HTTP_400_BAD_REQUEST)
            if order.paid:
                return Response({'status': 'SUCCESS'}, status=status.HTTP_200_OK)

            models.RazorpayCallback.objects.create(
                razorpay_order_id=params_dict['razorpay_order_id'],
                razorpay_payment_id=params_dict['razorpay_payment_id'],
                razorpay_signature=params_dict['razorpay_signature'],
                product_type=models.ProductTypes.STORAGE,
                institute_storage_license_order_details=order)

            try:
                client.utility.verify_payment_signature(params_dict)
                order.paid = True
                order.payment_date = int(time.time()) * 1000
                order.active = True
                order.start_date = int(time.time()) * 1000
                end_date = datetime.datetime.now() + datetime.timedelta(days=order.months * 30)
                order.end_date = datetime.datetime.timestamp(end_date) * 1000
                order.save()

                license_stat = models.InstituteLicenseStat.objects.filter(
                    institute__pk=order.institute.pk
                ).only('total_storage', 'storage_license_end_date').first()
                license_stat.total_storage += order.no_of_gb

                if license_stat.storage_license_end_date < order.end_date:
                    license_stat.storage_license_end_date = order.end_date

                license_stat.save()

                return Response({'status': _('SUCCESS')},
                                status=status.HTTP_200_OK)
            except SignatureVerificationError:
                return Response({'status': _('FAILURE')},
                                status=status.HTTP_200_OK)
        except Exception:
            msg = _('Internal server error. Dont worry, if payment was successful it will be verified automatically.' +\
                    'If problem persists let us know.')
            return Response({'error': msg},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class DeleteUnpaidStorageLicenseView(APIView):
    """View for deleting unpaid storage license"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def delete(self, *args, **kwargs):
        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug')
        ).only('institute_slug').first()

        if not institute:
            return Response({'error': _('Institute not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstitutePermission.objects.filter(
                institute=institute,
                invitee=self.request.user,
                active=True,
                role=models.InstituteRole.ADMIN
        ):
            return Response({'error': _('Permission denied [Admin only]')},
                            status=status.HTTP_400_BAD_REQUEST)

        order = models.InstituteStorageLicenseOrderDetails.objects.filter(
            pk=kwargs.get('license_order_id'),
            institute=institute,
            paid=False
        ).first()

        if not order:
            return Response(status.HTTP_204_NO_CONTENT)
        else:
            order.delete()
            return Response(status.HTTP_204_NO_CONTENT)


class StorageLicenseCredentialsForRetryPaymentView(APIView):
    """View for getting storage license credentials for retrying payment"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def get(self, *args, **kwargs):
        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug')
        ).only('institute_slug').first()

        if not institute:
            return Response({'error': _('Institute not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstitutePermission.objects.filter(
                institute=institute,
                invitee=self.request.user,
                active=True,
                role=models.InstituteRole.ADMIN
        ):
            return Response({'error': _('Permission denied [Admin only]')},
                            status=status.HTTP_400_BAD_REQUEST)

        order = models.InstituteStorageLicenseOrderDetails.objects.filter(
            pk=kwargs.get('license_order_id'),
            institute=institute,
            paid=False
        ).first()

        if not order:
            return Response({'error': _('Order not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        return Response({
            'id': order.pk,
            'order_receipt': order.order_receipt,
            'no_of_gb': order.no_of_gb,
            'months': order.months,
            'price': order.price,
            'gst_percent': order.gst_percent,
            'order_created_on': order.order_created_on,
            'amount': order.amount,
        }, status=status.HTTP_200_OK)


class InstituteCommonLicenseListView(ListAPIView):
    """
    View for getting list of all available common institute license
    """
    authentication_classes = (TokenAuthentication, )
    permission_classes = (IsAuthenticated, IsTeacher)
    serializer_class = serializer.InstituteLicenseListSerializer

    def get(self, *args, **kwargs):
        licenses = models.InstituteCommonLicense.objects.all()
        monthly_license = licenses.filter(
            billing=models.Billing.MONTHLY).order_by('type')
        yearly_license = licenses.filter(
            billing=models.Billing.ANNUALLY).order_by('type')

        monthly_license_list = []
        yearly_license_list = []

        for _license in monthly_license:
            ser = self.serializer_class(_license)
            monthly_license_list.append(ser.data)

        for _license in yearly_license:
            ser = self.serializer_class(_license)
            yearly_license_list.append(ser.data)

        if not len(monthly_license_list) and not len(yearly_license_list):
            return Response({'error': _('This product is currently not available.')},
                            status=status.HTTP_400_BAD_REQUEST)

        return Response({
            'monthly_license': monthly_license_list,
            'yearly_license': yearly_license_list
        }, status=status.HTTP_200_OK)


class InstituteCommonLicenseDetailView(APIView):
    """
    View for getting institute common license details
    """
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def post(self, request, *args, **kwargs):
        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug')
        ).only('institute_slug').first()

        if not institute:
            return Response({'error': _('Institute not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstitutePermission.objects.filter(
                institute=institute,
                invitee=self.request.user,
                role=models.InstituteRole.ADMIN,
                active=True
        ).exists():
            return Response({'error': _('Permission denied.')},
                            status=status.HTTP_400_BAD_REQUEST)

        models.InstituteSelectedCommonLicense.objects.filter(
            institute=institute,
            payment_id_generated=False
        ).delete()

        try:
            id_ = int(request.data.get('id'))
        except Exception:
            return Response({'id': _('Invalid id.')})

        if not id_:
            return Response({'error': _('Id is required.')})

        if not models.InstitutePermission.objects.filter(
            invitee=self.request.user.pk,
            role=models.InstituteRole.ADMIN
        ).exists():
            return Response({'error': _('Unauthorized request.')},
                            status=status.HTTP_400_BAD_REQUEST)

        license_ = models.InstituteCommonLicense.objects.filter(pk=id_).values()

        if license_:
            return Response(license_[0], status=status.HTTP_200_OK)
        else:
            return Response({
                'error': 'License not Found'
            }, status=status.HTTP_400_BAD_REQUEST)


class InstituteSelectCommonLicenseView(APIView):
    """View for selecting institute common license"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def post(self, request, *args, **kwargs):
        institute_slug = request.data.get('institute_slug')
        license_id = request.data.get('license_id')
        coupon_code = request.data.get('coupon_code')

        errors = {}
        if not institute_slug:
            errors['institute_slug'] = _('This field is required.')
        if not license_id:
            errors['license_id'] = _('This field is required.')

        if errors:
            return Response(
                errors, status=status.HTTP_400_BAD_REQUEST)

        institute = models.Institute.objects.filter(
            institute_slug=institute_slug
        ).only('institute_slug').first()

        if not institute:
            return Response({'error': _('Institute not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstitutePermission.objects.filter(
            institute=institute,
            invitee=self.request.user,
            active=True,
            role=models.InstituteRole.ADMIN
        ).exists():
            return Response({'error': _('Insufficient permission.')},
                            status=status.HTTP_400_BAD_REQUEST)

        coupon = None
        if coupon_code:
            coupon = models.InstituteDiscountCoupon.objects.filter(
                coupon_code=coupon_code
            ).first()

            if not coupon.active:
                return Response({'coupon_code': _('Coupon already used.')},
                                status=status.HTTP_400_BAD_REQUEST)
            if int(time.time()) > coupon.expiry_date:
                return Response({'coupon_code': _('Coupon expired.')},
                                status=status.HTTP_400_BAD_REQUEST)

        license_ = models.InstituteCommonLicense.objects.filter(
            pk=license_id
        ).first()
        if not license_:
            return Response({'error': _('License not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            sel_lic = models.InstituteSelectedCommonLicense.objects.create(
                institute=institute,
                type=license_.type,
                billing=license_.billing,
                price=license_.price,
                discount_percent=license_.discount_percent,
                discount_coupon=coupon,
                gst_percent=license_.gst_percent,
                no_of_admin=license_.no_of_admin,
                no_of_staff=license_.no_of_staff,
                no_of_faculty=license_.no_of_faculty,
                no_of_student=license_.no_of_student,
                no_of_board_of_members=license_.no_of_board_of_members,
                video_call_max_attendees=license_.video_call_max_attendees,
                classroom_limit=license_.classroom_limit,
                department_limit=license_.department_limit,
                subject_limit=license_.subject_limit,
                digital_test=license_.digital_test,
                LMS_exists=license_.LMS_exists,
                CMS_exists=license_.CMS_exists,
                discussion_forum=license_.discussion_forum,
                created_on=request.data.get('current_time')
            )
            return Response({'status': _('SUCCESS'),
                             'net_amount': sel_lic.net_amount,
                             'selected_license_id': sel_lic.id},
                            status=status.HTTP_200_OK)
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            return Response({'error': _('Internal server error.')},
                            status=status.HTTP_400_BAD_REQUEST)


class InstituteCreateCommonLicenseOrderView(APIView):
    """View for creating common license order"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def post(self, request, *args, **kwargs):
        institute_slug = request.data.get('institute_slug')
        license_id = request.data.get('license_id')
        payment_gateway = request.data.get('payment_gateway')

        errors = {}
        if not institute_slug:
            errors['institute_slug'] = _('This field is required.')
        if not license_id:
            errors['license_id'] = _('This field is required.')
        if not payment_gateway:
            errors['payment_gateway'] = _('This field is required.')

        if errors:
            return Response(
                errors, status=status.HTTP_400_BAD_REQUEST)

        institute = models.Institute.objects.filter(
            institute_slug=institute_slug
        ).only('institute_slug').first()

        if not institute:
            return Response({'error': _('Institute not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstitutePermission.objects.filter(
                institute=institute,
                invitee=self.request.user,
                active=True,
                role=models.InstituteRole.ADMIN
        ):
            return Response({'error': _('Insufficient permission.')},
                            status=status.HTTP_400_BAD_REQUEST)

        license_ = models.InstituteSelectedCommonLicense.objects.filter(
            pk=license_id,
            institute=institute
        ).first()

        if not license_:
            return Response({'error': _('Selected license not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if payment_gateway != models.PaymentGateway.RAZORPAY:
            return Response({'error': _('Payment gateway not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        prev_order = models.InstituteCommonLicenseOrderDetails.objects.filter(
            institute=institute,
            selected_license=license_
        ).first()

        if prev_order:
            if prev_order.payment_gateway != payment_gateway:
                prev_order.payment_gateway = payment_gateway
                # Generate order with new payment gateway
                prev_order.save()

            if prev_order.payment_gateway == models.PaymentGateway.RAZORPAY:
                return Response(
                    {'status': 'SUCCESS',
                     'amount': prev_order.amount,
                     'key_id': os.environ.get('RAZORPAY_TEST_KEY_ID'),
                     'currency': prev_order.currency,
                     'order_id': prev_order.order_id,
                     'order_details_id': prev_order.pk,
                     'email': str(self.request.user),
                     'contact': str(self.request.user.user_profile.phone),
                     'type': prev_order.selected_license.type
                     }, status=status.HTTP_201_CREATED)
            else:
                pass  # Generate appropriate response

        try:
            order = models.InstituteCommonLicenseOrderDetails.objects.create(
                institute=institute,
                payment_gateway=payment_gateway,
                selected_license=license_
            )
            license_.payment_id_generated = True
            license_.save()

            if order.payment_gateway == models.PaymentGateway.RAZORPAY:
                return Response(
                    {'status': 'SUCCESS',
                     'amount': order.amount,
                     'key_id': os.environ.get('RAZORPAY_TEST_KEY_ID'),
                     'currency': order.currency,
                     'order_id': order.order_id,
                     'order_details_id': order.pk,
                     'email': str(self.request.user),
                     'contact': str(self.request.user.user_profile.phone),
                     'type': order.selected_license.type},
                    status=status.HTTP_201_CREATED)
            else:
                pass  # Generate response
        except Exception as e:
            print(e)
            return Response({'error': _('Internal server error.')},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class RazorpayCommonLicensePaymentCallbackView(APIView):
    """
    View for storing payment callback data
    and checking whether payment was successful
    """
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def post(self, request, *args, **kwargs):
        params_dict = {
            'razorpay_order_id': request.data.get('razorpay_order_id'),
            'razorpay_payment_id': request.data.get('razorpay_payment_id'),
            'razorpay_signature': request.data.get('razorpay_signature')
        }
        order_details_id = request.data.get('order_details_id')

        if not params_dict['razorpay_order_id'] or\
                not params_dict['razorpay_payment_id'] or\
                not params_dict['razorpay_signature'] or\
                not order_details_id:
            return Response({'error': _('Invalid fields. Contact support.')},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            order = models.InstituteCommonLicenseOrderDetails.objects.filter(pk=order_details_id).first()
            if not order:
                return Response({
                    'error': _('Order not found. If payment is successful it will be verified automatically.')},
                                status=status.HTTP_400_BAD_REQUEST)
            if order.paid:
                return Response({'status': _('SUCCESS')}, status=status.HTTP_200_OK)

            models.RazorpayCallback.objects.create(
                razorpay_order_id=params_dict['razorpay_order_id'],
                razorpay_payment_id=params_dict['razorpay_payment_id'],
                razorpay_signature=params_dict['razorpay_signature'],
                product_type=models.ProductTypes.LMS_CMS_EXAM_LIVE_STREAM,
                institute_common_license_order_details=order)

            try:
                client.utility.verify_payment_signature(params_dict)
                order.paid = True
                order.payment_date = int(time.time()) * 1000
                order.active = True
                order.start_date = int(time.time()) * 1000
                days = 0

                if order.selected_license.billing == models.Billing.MONTHLY:
                    days = 30
                else:
                    days = 365

                end_date = datetime.datetime.now() + datetime.timedelta(days=days)
                order.end_date = datetime.datetime.timestamp(end_date) * 1000
                order.save()

                return Response({'status': _('SUCCESS')},
                                status=status.HTTP_200_OK)
            except SignatureVerificationError:
                return Response({'status': _('FAILURE')},
                                status=status.HTTP_200_OK)
        except Exception as e:
            print(e)
            return Response({'error': _('Internal server error. Dont worry, if payment was successful it will be verified automatically. If problem persists let us know.')},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class RazorpayWebhookCallbackView(APIView):

    def post(self, request, *args, **kwargs):
        request_body = request.body.decode('utf-8')
        try:
            razorpay_order_id = request.data['payload']['payment']['entity']['order_id']
            razorpay_payment_id = request.data['payload']['payment']['entity']['id']
            order = models.InstituteCommmonLicenseOrderDetails.objects.filter(
                order_id=razorpay_order_id).first()

            if not order:
                return Response({'status': 'Order does not exist'}, status=status.HTTP_400_BAD_REQUEST)

            if order.paid:
                return Response({'status': 'ok'}, status=status.HTTP_200_OK)
        except Exception:
            return Response({'status': 'failed'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            client.utility.verify_webhook_signature(
                request_body,
                request.META.get('HTTP_X_RAZORPAY_SIGNATURE'),
                os.environ.get('RAZORPAY_WEBHOOK_SECRET'))
            order.paid = True
            order.payment_date = int(time.time()) * 1000
            order.save()
            models.RazorpayWebHookCallback.objects.create(
                order_id=razorpay_order_id,
                razorpay_payment_id=razorpay_payment_id
            )
            return Response({'status': 'ok'}, status=status.HTTP_200_OK)
        except SignatureVerificationError:
            return Response({'status': 'failed'}, status=status.HTTP_400_BAD_REQUEST)


class InstituteOrderedLicenseOrderDetailsView(APIView):
    """View for getting list of license orders"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def get(self, *args, **kwargs):
        """
        View for getting active_license, expired_license
        and purchased_inactive_license details by admin.
        """
        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug')
        ).only('institute_slug').first()

        if not institute:
            return Response({'error': _('Institute not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstitutePermission.objects.filter(
            institute=institute,
            invitee=self.request.user,
            active=True,
            role=models.InstituteRole.ADMIN
        ).exists():
            return Response({'error': _('Insufficient permission.')},
                            status=status.HTTP_400_BAD_REQUEST)

        response = dict()
        response['active_license'] = list()
        response['expired_license'] = list()
        response['pending_payment_license'] = list()

        if kwargs.get('product_type') == models.ProductTypes.LMS_CMS_EXAM_LIVE_STREAM:
            # Updating the orders
            # De-activating order if expired
            for order in models.InstituteCommonLicenseOrderDetails.objects.filter(
                institute=institute,
                paid=True,
                active=True
            ):
                if int(time.time()) * 1000 > order.end_date:
                    query = models.InstituteCommonLicenseOrderDetails.objects.filter(
                        pk=order.pk
                    ).first()
                    query.active = False
                    query.save()

            # Deleting unpaid orders if its not paid for more than 14 days
            for order in models.InstituteCommonLicenseOrderDetails.objects.filter(
                    institute=institute,
                    paid=False,
                    active=False
            ).only('order_created_on', 'selected_license'):
                if int(time.time()) * 1000 - order.order_created_on > 86400 * 1000 * 14:
                    models.InstituteSelectedCommonLicense.objects.filter(
                        pk=order.selected_license.pk
                    ).delete()

            orders = models.InstituteCommonLicenseOrderDetails.objects.filter(
                institute=institute
            )
            active_license = orders.filter(
                paid=True,
                active=True,
                end_date__gte=int(time.time()) * 1000).order_by('-end_date')
            expired_license = orders.filter(
                paid=True,
                active=False,
                end_date__lte=int(time.time()) * 1000).order_by('-end_date')
            pending_payment_license = orders.filter(
                paid=False).order_by('-order_created_on')

            if active_license:
                for al in active_license:
                    response['active_license'].append({
                        'order_receipt': al.order_receipt,
                        'payment_date': al.payment_date,
                        'start_date': al.start_date,
                        'end_date': al.end_date,
                        'selected_license_id': al.selected_license.pk,
                        'type': al.selected_license.type,
                        'billing': al.selected_license.billing,
                        'amount': al.amount,
                        'order_pk': al.pk
                    })

            if expired_license:
                for el in expired_license:
                    response['expired_license'].append({
                        'order_receipt': el.order_receipt,
                        'payment_date': el.payment_date,
                        'start_date': el.start_date,
                        'end_date': el.end_date,
                        'selected_license_id': el.selected_license.pk,
                        'type': el.selected_license.type,
                        'billing': el.selected_license.billing,
                        'amount': el.amount
                    })

            if pending_payment_license:
                for pp in pending_payment_license:
                    response['pending_payment_license'].append({
                        'order_created_on': pp.order_created_on,
                        'order_receipt': pp.order_receipt,
                        'selected_license_id': pp.selected_license.pk,
                        'type': pp.selected_license.type,
                        'billing': pp.selected_license.billing,
                        'amount': pp.amount,
                        'order_pk': pp.pk
                    })
        elif kwargs.get('product_type') == models.ProductTypes.STORAGE:
            storage = 0
            # Updating the orders
            # Setting expired orders to inactive
            for order in models.InstituteStorageLicenseOrderDetails.objects.filter(
                    institute=institute,
                    paid=True,
                    active=True
            ).only('active', 'end_date'):
                if int(time.time()) * 1000 > order.end_date:
                    query = models.InstituteStorageLicenseOrderDetails.objects.filter(
                        pk=order.pk
                    ).only('active').first()
                    query.active = False
                    query.save()

                    storage += float(query.no_of_gb)

            if storage > 0:
                lic = models.InstituteLicenseStat.objects.filter(
                    institute=institute
                ).only('total_storage').first()

                lic.total_storage = max(0.0, float(lic.total_storage) - storage)
                lic.save()

            # Deleting unpaid orders if its not paid for more than 14 days
            for order in models.InstituteStorageLicenseOrderDetails.objects.filter(
                    institute=institute,
                    paid=False,
                    active=False
            ).only('order_created_on'):
                if int(time.time()) * 1000 - order.order_created_on > 86400 * 1000 * 14:
                    models.InstituteStorageLicenseOrderDetails.objects.filter(
                        pk=order.pk
                    ).delete()

            orders = models.InstituteStorageLicenseOrderDetails.objects.filter(
                institute=institute
            )
            active_license = orders.filter(
                paid=True,
                active=True,
                end_date__gte=int(time.time()) * 1000).order_by('-end_date')
            expired_license = orders.filter(
                paid=True,
                active=False,
                end_date__lte=int(time.time()) * 1000).order_by('-end_date')
            pending_payment_license = orders.filter(
                paid=False).order_by('-order_created_on')

            if active_license:
                for al in active_license:
                    response['active_license'].append({
                        'order_receipt': al.order_receipt,
                        'payment_date': al.payment_date,
                        'start_date': al.start_date,
                        'end_date': al.end_date,
                        'no_of_gb': al.no_of_gb,
                        'months': al.months,
                        'amount': al.amount,
                        'order_pk': al.pk
                    })

            if expired_license:
                for el in expired_license:
                    response['expired_license'].append({
                        'order_receipt': el.order_receipt,
                        'payment_date': el.payment_date,
                        'start_date': el.start_date,
                        'end_date': el.end_date,
                        'no_of_gb': el.no_of_gb,
                        'months': el.months,
                        'amount': el.amount
                    })

            if pending_payment_license:
                for pp in pending_payment_license:
                    response['pending_payment_license'].append({
                        'order_created_on': pp.order_created_on,
                        'order_receipt': pp.order_receipt,
                        'no_of_gb': pp.no_of_gb,
                        'months': pp.months,
                        'amount': pp.amount,
                        'cost_per_gb': pp.price,
                        'order_pk': pp.pk
                    })

        elif kwargs.get('product_type') == models.ProductTypes.DIGITAL_EXAM:
            pass
        elif kwargs.get('product_type') == models.ProductTypes.LIVE_STREAM:
            pass

        return Response(response, status=status.HTTP_200_OK)


class InstituteSelectedCommonLicenseDetailsView(APIView):
    """View for getting institute selected common license details"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def get(self, *args, **kwargs):
        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug')
        ).only('institute_slug').first()

        if not institute:
            return Response({'error': _('Institute not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstitutePermission.objects.filter(
                institute=institute,
                invitee=self.request.user,
                active=True,
                role=models.InstituteRole.ADMIN
        ).exists():
            return Response({'error': _('Insufficient permission.')},
                            status=status.HTTP_400_BAD_REQUEST)

        license_ = models.InstituteSelectedCommonLicense.objects.filter(
            pk=kwargs.get('selected_license_id'),
            institute=institute
        ).first()

        if not license_:
            return Response({'error': _('License not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        response = {
            'type': license_.type,
            'billing': license_.billing,
            'price': license_.price,
            'discount_percent': license_.discount_percent,
            'gst_percent': license_.gst_percent,
            'no_of_admin': license_.no_of_admin,
            'no_of_staff': license_.no_of_staff,
            'no_of_faculty': license_.no_of_faculty,
            'no_of_student': license_.no_of_student,
            'no_of_board_of_members': license_.no_of_board_of_members,
            'video_call_max_attendees': license_.video_call_max_attendees,
            'classroom_limit': license_.classroom_limit,
            'department_limit': license_.department_limit,
            'subject_limit': license_.subject_limit,
            'digital_test': license_.digital_test,
            'LMS_exists': license_.LMS_exists,
            'CMS_exists': license_.CMS_exists,
            'discussion_forum': license_.discussion_forum
        }

        if license_.discount_coupon:
            response['discount_coupon'] = license_.discount_coupon.coupon_code
            response['discount_rs'] = license_.discount_coupon.discount_rs
        else:
            response['discount_coupon'] = ''

        return Response(response, status=status.HTTP_200_OK)


class DeleteUnpaidCommonLicenseView(APIView):
    """View for deleting unpaid common license"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def delete(self, *args, **kwargs):
        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug')
        ).only('institute_slug').first()

        if not institute:
            return Response({'error': _('Institute not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstitutePermission.objects.filter(
                institute=institute,
                invitee=self.request.user,
                active=True,
                role=models.InstituteRole.ADMIN
        ):
            return Response({'error': _('Permission denied [Admin only]')},
                            status=status.HTTP_400_BAD_REQUEST)

        order = models.InstituteCommonLicenseOrderDetails.objects.filter(
            pk=kwargs.get('license_order_id'),
            institute=institute,
            paid=False
        ).only('selected_license').first()

        if not order:
            return Response(status.HTTP_204_NO_CONTENT)
        else:
            models.InstituteSelectedCommonLicense.objects.filter(
                pk=order.selected_license.pk
            ).delete()
            return Response(status.HTTP_204_NO_CONTENT)


class CommonLicenseCredentialsForRetryPaymentView(APIView):
    """View for getting common license credentials for retrying payment"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def get(self, *args, **kwargs):
        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug')
        ).only('institute_slug').first()

        if not institute:
            return Response({'error': _('Institute not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstitutePermission.objects.filter(
                institute=institute,
                invitee=self.request.user,
                active=True,
                role=models.InstituteRole.ADMIN
        ):
            return Response({'error': _('Permission denied [Admin only]')},
                            status=status.HTTP_400_BAD_REQUEST)

        order = models.InstituteCommonLicenseOrderDetails.objects.filter(
            pk=kwargs.get('license_order_id'),
            institute=institute,
            paid=False
        ).first()

        if not order:
            return Response({'error': _('Order not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        response = {
            'id': order.pk,
            'order_receipt': order.order_receipt,
            'type': order.selected_license.type,
            'billing': order.selected_license.billing,
            'price': order.selected_license.price,
            'discount_percent': order.selected_license.discount_percent,
            'gst_percent': order.selected_license.gst_percent,
            'order_created_on': order.order_created_on,
            'amount': order.amount,
            'no_of_admin': order.selected_license.no_of_admin,
            'no_of_staff': order.selected_license.no_of_staff,
            'no_of_faculty': order.selected_license.no_of_faculty,
            'no_of_student': order.selected_license.no_of_student,
            'no_of_board_of_members': order.selected_license.no_of_board_of_members,
            'video_call_max_attendees': order.selected_license.video_call_max_attendees,
            'classroom_limit': order.selected_license.classroom_limit,
            'department_limit': order.selected_license.department_limit,
            'subject_limit': order.selected_license.subject_limit,
            'LMS_exists': order.selected_license.LMS_exists,
            'CMS_exists': order.selected_license.CMS_exists,
            'discussion_forum': order.selected_license.discussion_forum,
            'selected_license_id': order.selected_license.pk
        }
        
        if order.selected_license.discount_coupon:
            response['discount_coupon_code'] = order.selected_license.discount_coupon.coupon_code
            response['discount_rs'] = order.selected_license.discount_rs

        return Response(response, status=status.HTTP_200_OK)


class InstituteMinLicenseStatisticsView(APIView):
    """View for returning minimum license statistics of the institute"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def get(self, *args, **kwargs):
        """
        If common license was purchased ever then it returns true for common license
        """
        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug')
        ).only('institute_slug').first()

        if not institute:
            return Response({'error': _('Institute not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstitutePermission.objects.filter(
            institute=institute,
            invitee=self.request.user,
            active=True
        ).exists():
            return Response({'error': _('Permission denied.')},
                            status=status.HTTP_400_BAD_REQUEST)

        response = {
            'purchased_common_license': False
        }
        if models.InstituteCommonLicenseOrderDetails.objects.filter(
            institute=institute,
            paid=True
        ).exists():
            response['purchased_common_license'] = True

        return Response(response, status=status.HTTP_200_OK)


class InstituteMinDetailsStudentView(ListAPIView):
    """
    View for getting min details of institute by student
    """
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsStudent)
    serializer_class = serializer.InstituteMinDetailsStudentSerializer
    queryset = models.Institute.objects.all()

    def get_serializer(self, *args, **kwargs):
        serializer_class = self.get_serializer_class()
        context = self.get_serializer_context()
        context['user'] = self.request.user
        kwargs['context'] = context
        return serializer_class(*args, **kwargs)

    def list(self, request, *args, **kwargs):
        institutes = models.InstituteStudents.objects.filter(
            invitee=self.request.user
        ).only('institute').order_by('-created_on')
        active_student_institutes = institutes.filter(active=True)
        invited_student_institutes = institutes.filter(active=False)

        response = {
            'active_institutes': [],
            'invited_institutes': []
        }
        for institute in active_student_institutes:
            queryset = self.queryset.filter(
                pk=institute.institute.pk
            ).first()
            ser = self.get_serializer(queryset)
            response['active_institutes'].append(ser.data)

        for institute in invited_student_institutes:
            queryset = self.queryset.filter(
                pk=institute.institute.pk
            ).first()
            ser = self.get_serializer(queryset)
            response['invited_institutes'].append(ser.data)

        return Response(response, status=status.HTTP_200_OK)


class InstituteMinDetailsTeacherView(ListAPIView):
    """
    View for getting the min details of institute
    by admin teacher
    """
    authentication_classes = (TokenAuthentication, )
    permission_classes = (IsAuthenticated, IsTeacher)
    serializer_class = serializer.InstituteMinDetailsSerializer
    queryset = models.Institute.objects.all()

    def get_queryset(self):
        return self.queryset.filter(user=self.request.user)

    def get_serializer(self, *args, **kwargs):
        """
        Return the serializer instance that should be used for validating and
        deserializing input, and for serializing output.
        """
        serializer_class = self.get_serializer_class()
        context = self.get_serializer_context()
        context['user'] = self.request.user
        kwargs['context'] = context
        return serializer_class(*args, **kwargs)


class InstituteJoinedMinDetailsTeacherView(ListAPIView):
    """
    View for getting the min details of joined institutes"""
    authentication_classes = (TokenAuthentication, )
    permission_classes = (IsAuthenticated, IsTeacher)
    serializer_class = serializer.InstitutesJoinedMinDetailsTeacher
    queryset = models.Institute.objects.all()

    def get_queryset(self):
        return self.queryset.filter(
            permissions__invitee=self.request.user.pk,
            permissions__active=True).exclude(
            permissions__inviter=self.request.user.pk
        )

    def get_serializer(self, *args, **kwargs):
        """
        Return the serializer instance that should be used for validating and
        deserializing input, and for serializing output.
        """
        serializer_class = self.get_serializer_class()
        context = self.get_serializer_context()
        context['user'] = self.request.user
        kwargs['context'] = context
        return serializer_class(*args, **kwargs)


class InstitutePendingInviteMinDetailsTeacherView(ListAPIView):
    """View for getting the min details of active invites by institutes"""
    authentication_classes = (TokenAuthentication, )
    permission_classes = (IsAuthenticated, IsTeacher)
    serializer_class = serializer.InstitutePendingInviteMinDetailsSerializer
    queryset = models.Institute.objects.all()

    def get_queryset(self):
        return self.queryset.filter(
            permissions__invitee=self.request.user.pk,
            permissions__active=False).exclude(
            permissions__inviter=self.request.user.pk
        )

    def get_serializer(self, *args, **kwargs):
        """
        Return the serializer instance that should be used for validating and
        deserializing input, and for serializing output.
        """
        serializer_class = self.get_serializer_class()
        context = self.get_serializer_context()
        context['user'] = self.request.user
        kwargs['context'] = context
        return serializer_class(*args, **kwargs)


class AddStudentToInstituteView(APIView):
    """View for adding user to institute by admin"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def post(self, request, *args, **kwargs):
        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug')
        ).only('institute_slug').first()

        if not institute:
            return Response({'error': _('Institute not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstitutePermission.objects.filter(
            institute=institute,
            invitee=self.request.user,
            active=True,
            role=models.InstituteRole.ADMIN
        ).exists():
            return Response({'error': _('Permission denied.')},
                            status=status.HTTP_400_BAD_REQUEST)

        order = get_active_common_license(institute)
        if not order:
            return Response({'error': _('License not found or expired.')},
                            status=status.HTTP_400_BAD_REQUEST)

        invitee = get_user_model().objects.filter(
            email=request.data.get('invitee_email')
        ).first()

        if not invitee:
            return Response({'error': _('No student with this email was found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not invitee.is_student:
            return Response({'error': _('User is not a student.')},
                            status=status.HTTP_400_BAD_REQUEST)

        class_ = None
        if request.data.get('class'):
            class_ = models.InstituteClass.objects.filter(
                class_slug=request.data.get('class'),
                class_institute__pk=institute.pk
            ).only('class_slug').first()
            if not class_:
                return Response({'error': _('Class may have been deleted. Please refresh and try again.')},
                                status=status.HTTP_400_BAD_REQUEST)

        try:
            student = models.InstituteStudents.objects.create(
                invitee=invitee,
                inviter=self.request.user,
                institute=institute,
                enrollment_no=request.data.get('enrollment_no'),
                registration_no=request.data.get('registration_no'),
                first_name=request.data.get('first_name'),
                last_name=request.data.get('last_name'),
                gender=request.data.get('gender'),
                date_of_birth=request.data.get('date_of_birth')
            )
            if class_:
                models.InstituteClassStudents.objects.create(
                    institute_class=class_,
                    institute_student=student,
                    inviter=self.request.user,
                )
            response = {
                'id': student.pk,
                'invitee_email': str(student.invitee),
                'first_name': student.first_name,
                'last_name': student.last_name,
                'gender': student.gender,
                'date_of_birth': student.date_of_birth,
                'enrollment_no': student.enrollment_no,
                'registration_no': student.registration_no,
                'created_on': student.created_on,
                'image': '',
            }

            if class_:
                response['class_name'] = class_.name

            return Response(response, status=status.HTTP_201_CREATED)
        except IntegrityError:
            return Response({'error': _('Student was already invited.')},
                            status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            return Response({'error': _('Unknown error occurred.')},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class EditInstituteStudentDetailsView(APIView):
    """View for editing student details by admin"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def patch(self, request, *args, **kwargs):
        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug')
        ).first()

        if not institute:
            return Response({'error': _('Institute not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstitutePermission.objects.filter(
            institute=institute,
            invitee=self.request.user,
            active=True
        ).filter(
            Q(role=models.InstituteRole.ADMIN) | Q(role=models.InstituteRole.STAFF),
        ).exists():
            return Response({'error': _('Permission denied.')},
                            status=status.HTTP_400_BAD_REQUEST)

        student_details = models.InstituteStudents.objects.filter(
            pk=request.data.get('id'),
            institute=institute
        ).defer('edited', 'institute', 'inviter').first()

        if not student_details:
            return Response({'error': _('Student may have been removed.')},
                            status=status.HTTP_400_BAD_REQUEST)

        student_details.first_name = request.data.get('first_name')
        student_details.last_name = request.data.get('last_name')
        student_details.enrollment_no = request.data.get('enrollment_no')
        student_details.registration_no = request.data.get('registration_no')
        student_details.gender = request.data.get('gender')
        student_details.date_of_birth = request.data.get('date_of_birth')
        student_details.save()

        response = dict()
        response['id'] = student_details.pk
        response['invitee_email'] = str(student_details.invitee)
        response['enrollment_no'] = student_details.enrollment_no
        response['registration_no'] = student_details.registration_no
        response['first_name'] = student_details.first_name
        response['last_name'] = student_details.last_name
        response['gender'] = student_details.gender
        response['date_of_birth'] = student_details.date_of_birth
        response['created_on'] = student_details.created_on
        if student_details.active:
            response['is_banned'] = student_details.is_banned
        response['image'] = ''
        return Response(response, status=status.HTTP_200_OK)


class InstituteStudentListView(APIView):
    """View for getting active and invited student list by permitted user"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def get(self, *args, **kwargs):
        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug')
        ).only('institute_slug').first()

        if not institute:
            return Response({'error': _('Institute not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        requester_perm = models.InstitutePermission.objects.filter(
            institute=institute,
            invitee=self.request.user,
            active=True
        ).only('role').first()

        if not requester_perm:
            return Response({'error': _('Permission denied.')},
                            status=status.HTTP_400_BAD_REQUEST)

        order = get_active_common_license(institute)
        if not order:
            return Response({'error': _('License not found or expired.')},
                            status=status.HTTP_400_BAD_REQUEST)

        student_list = None
        if kwargs.get('student_type') == 'active':
            student_list = models.InstituteStudents.objects.filter(
                institute__pk=institute.pk,
                active=True,
                is_banned=False
            ).defer('inviter', 'edited', 'is_banned').order_by('registration_no')
        elif kwargs.get('student_type') == 'inactive':
            student_list = models.InstituteStudents.objects.filter(
                institute__pk=institute.pk,
                active=False,
                is_banned=False
            ).defer('inviter', 'edited', 'is_banned').order_by('registration_no')
        elif kwargs.get('student_type') == 'banned':
            student_list = models.InstituteStudents.objects.filter(
                institute__pk=institute.pk,
                is_banned=True
            ).defer('inviter', 'edited', 'is_banned').order_by('registration_no')
        response = list()

        for s in student_list:
            res = dict()
            res['invitee_email'] = str(s.invitee)
            res['id'] = s.pk
            res['first_name'] = s.first_name
            res['last_name'] = s.last_name
            res['gender'] = s.gender
            res['date_of_birth'] = s.date_of_birth
            res['enrollment_no'] = s.enrollment_no
            res['registration_no'] = s.registration_no
            res['created_on'] = str(s.created_on)
            res['image'] = ''

            if s.is_banned:
                res['is_banned'] = s.is_banned
                ban_details = models.InstituteBannedStudent.objects.filter(
                    institute_student__invitee=s.invitee,
                    banned_institute__pk=institute.pk
                ).first()
                res['banning_reason'] = ban_details.reason
                res['banned_on'] = str(ban_details.created_on)
                res['ban_start_date'] = str(ban_details.start_date)
                res['active'] = s.active

                if ban_details.banned_by.user_profile.first_name:
                    first_name = ban_details.banned_by.user_profile.first_name
                    last_name = ban_details.banned_by.user_profile.last_name
                    res['banned_by'] = first_name + ' ' + last_name
                else:
                    res['banned_by'] = str(ban_details.banned_by)

                if ban_details.end_date:
                    res['ban_end_date'] = str(ban_details.end_date)

            class_invite = models.InstituteClassStudents.objects.filter(
                institute_student__invitee__pk=s.invitee.pk
            ).only('institute_class').first()

            if class_invite:
                res['class_name'] = class_invite.institute_class.name

            response.append(res)

        return Response({
            'data': response,
            'requester_role': requester_perm.role
        }, status=status.HTTP_200_OK)


class AddStudentToClassView(APIView):
    """View for adding user to class by admin or class staff"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def post(self, request, *args, **kwargs):
        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug')
        ).only('name').first()

        if not institute:
            return Response({'error': _('Institute not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        class_ = models.InstituteClass.objects.filter(
            class_slug=kwargs.get('class_slug')
        ).only('name').first()

        if not class_:
            return Response({'error': _('Class not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteClassPermission.objects.filter(
            invitee=self.request.user,
            to__pk=class_.pk
        ).exists():
            if not models.InstitutePermission.objects.filter(
                invitee=self.request.user,
                institute__pk=institute.pk,
                active=True,
                role=models.InstituteRole.ADMIN
            ).exists():
                return Response({'error': _('Permission denied.')},
                                status=status.HTTP_400_BAD_REQUEST)

        order = get_active_common_license(institute)
        if not order:
            return Response({'error': _('License not found or expired.')},
                            status=status.HTTP_400_BAD_REQUEST)

        invitee = get_user_model().objects.filter(
            email=request.data.get('invitee_email')
        ).only('is_student').first()

        if not invitee:
            return Response({'error': _('No student with this email was found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not invitee.is_student:
            return Response({'error': _('User is not a student.')},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            institute_student = models.InstituteStudents.objects.filter(
                invitee__pk=invitee.pk,
                institute__pk=institute.pk
            ).only('first_name', 'last_name', 'enrollment_no',
                   'registration_no', 'gender', 'date_of_birth').first()
            active_student = False

            if institute_student:
                active_student = True
                if request.data.get('enrollment_no'):
                    institute_student.enrollment_no = request.data.get('enrollment_no')
                    institute_student.save()
            else:
                institute_student = models.InstituteStudents.objects.create(
                    invitee=invitee,
                    inviter=self.request.user,
                    institute=institute,
                    enrollment_no=request.data.get('enrollment_no')
                )
            class_student = models.InstituteClassStudents.objects.create(
                institute_class=class_,
                institute_student=institute_student,
                inviter=self.request.user,
                active=active_student
            )

            response = {
                'id': institute_student.pk,
                'invitee_email': str(institute_student.invitee),
                'first_name': institute_student.first_name,
                'last_name': institute_student.last_name,
                'gender': institute_student.gender,
                'date_of_birth': institute_student.date_of_birth,
                'enrollment_no': institute_student.enrollment_no,
                'registration_no': institute_student.registration_no,
                'created_on': str(class_student.created_on),
                'image': '',
                'active': class_student.active
            }

            return Response(response, status=status.HTTP_201_CREATED)
        except IntegrityError:
            return Response({'error': _('Student was already invited.')},
                            status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            return Response({'error': _('Unknown error occurred.')},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class InstituteClassStudentListView(APIView):
    """
    View for getting active and inactive student list
    by permitted user (admin and class staff)
    """
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def get(self, *args, **kwargs):
        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug')
        ).only('name').first()

        if not institute:
            return Response({'error': _('Institute not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        class_ = models.InstituteClass.objects.filter(
            class_slug=kwargs.get('class_slug')
        ).only('class_institute').first()

        if not class_:
            return Response({'error': _('Class not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        has_class_perm = False
        requester_perm = models.InstitutePermission.objects.filter(
            institute__pk=institute.pk,
            invitee=self.request.user,
            active=True
        ).only('role').first()

        if not requester_perm:
            return Response({'error': _('Permission denied.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if models.InstituteClassPermission.objects.filter(
            to__pk=class_.pk,
            invitee=self.request.user
        ).exists():
            has_class_perm = True

        order = get_active_common_license(institute)
        if not order:
            return Response({'error': _('License not found or expired.')},
                            status=status.HTTP_400_BAD_REQUEST)

        student_list = None
        if kwargs.get('student_type') == 'active':
            student_list = models.InstituteClassStudents.objects.filter(
                institute_class__pk=class_.pk,
                active=True,
                is_banned=False
            ).only('institute_student', 'created_on', 'is_banned').order_by(
                'institute_student__enrollment_no')
        elif kwargs.get('student_type') == 'inactive':
            student_list = models.InstituteClassStudents.objects.filter(
                institute_class__pk=class_.pk,
                active=False,
                is_banned=False
            ).only('institute_student', 'created_on', 'is_banned').order_by(
                'institute_student__enrollment_no')
        elif kwargs.get('student_type') == 'banned':
            student_list = models.InstituteClassStudents.objects.filter(
                institute_class__pk=class_.pk,
                active=True,
                is_banned=True
            ).only('institute_student', 'created_on', 'is_banned').order_by(
                'institute_student__enrollment_no')
        response = list()

        for s in student_list:
            res = dict()
            res['invitee_email'] = str(s.institute_student.invitee)
            res['created_on'] = s.created_on
            res['is_banned'] = s.is_banned

            res['id'] = s.institute_student.pk
            res['first_name'] = s.institute_student.first_name
            res['last_name'] = s.institute_student.last_name
            res['gender'] = s.institute_student.gender
            res['date_of_birth'] = s.institute_student.date_of_birth
            res['enrollment_no'] = s.institute_student.enrollment_no
            res['registration_no'] = s.institute_student.registration_no
            res['image'] = ''

            if s.is_banned:
                ban_details = models.InstituteClassBannedStudent.objects.filter(
                    institute_student__invitee=s.institute_student.invitee,
                    banned_class__pk=class_.pk,
                    active=True
                ).first()
                res['banning_reason'] = ban_details.reason
                res['banned_on'] = ban_details.created_on
                res['ban_start_date'] = ban_details.start_date

                if ban_details.banned_by.user_profile.first_name:
                    first_name = ban_details.banned_by.user_profile.first_name
                    last_name = ban_details.banned_by.user_profile.last_name
                    res['banned_by'] = first_name + ' ' + last_name
                else:
                    res['banned_by'] = ban_details.banned_by

                if ban_details.end_date != models.UNLIMITED:
                    res['ban_end_date'] = ban_details.end_date

            response.append(res)

        return Response({
            'data': response,
            'requester_role': requester_perm.role,
            'has_perm': has_class_perm
        }, status=status.HTTP_200_OK)


class AddStudentToSubjectView(APIView):
    """View for adding user to subject by permitted faculty, admin or class staff"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def post(self, request, *args, **kwargs):
        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug')
        ).only('name').first()

        if not institute:
            return Response({'error': _('Institute not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        class_ = models.InstituteClass.objects.filter(
            class_slug=kwargs.get('class_slug')
        ).only('name').first()

        if not class_:
            return Response({'error': _('Class not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug')
        ).only('name').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectPermission.objects.filter(
                invitee=self.request.user,
                to__pk=subject.pk,
        ).exists():
            if not models.InstituteClassPermission.objects.filter(
                    invitee=self.request.user,
                    to__pk=class_.pk
            ).exists():
                if not models.InstitutePermission.objects.filter(
                    invitee=self.request.user,
                    institute__pk=institute.pk,
                    role=models.InstituteRole.ADMIN,
                    active=True
                ).exists():
                    return Response({'error': _('Permission denied.')},
                                    status=status.HTTP_400_BAD_REQUEST)

        order = get_active_common_license(institute)
        if not order:
            return Response({'error': _('License not found or expired.')},
                            status=status.HTTP_400_BAD_REQUEST)

        invitee = get_user_model().objects.filter(
            email=request.data.get('invitee_email')
        ).first()

        if not invitee:
            return Response({'error': _('No student with this email was found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not invitee.is_student:
            return Response({'error': _('User is not a student.')},
                            status=status.HTTP_400_BAD_REQUEST)

        student_is_active = False

        try:
            institute_student = models.InstituteStudents.objects.filter(
                invitee=invitee,
                institute__pk=institute.pk
            ).only('first_name', 'last_name', 'enrollment_no',
                   'registration_no', 'gender', 'date_of_birth').first()

            if institute_student:
                student_is_active = True
            else:
                student_is_active = False
                institute_student = models.InstituteStudents.objects.create(
                    invitee=invitee,
                    inviter=self.request.user,
                    institute=institute
                )

            subject_student = None
            if not models.InstituteClassStudents.objects.filter(
                institute_class__pk=class_.pk,
                institute_student__invitee__pk=invitee.pk
            ).exists():
                models.InstituteClassStudents.objects.create(
                    institute_class=class_,
                    institute_student=institute_student,
                    inviter=self.request.user,
                    active=student_is_active
                )
                subject_student = models.InstituteSubjectStudents.objects.filter(
                    institute_subject__pk=subject.pk,
                    institute_student__invitee__pk=invitee.pk
                ).only('active', 'created_on').first()
            else:
                subject_student = models.InstituteSubjectStudents.objects.create(
                    institute_subject=subject,
                    institute_student=institute_student,
                    inviter=self.request.user,
                    active=student_is_active
                )
            response = {
                'id': institute_student.pk,
                'invitee_email': str(institute_student.invitee),
                'first_name': institute_student.first_name,
                'last_name': institute_student.last_name,
                'gender': institute_student.gender,
                'date_of_birth': institute_student.date_of_birth,
                'enrollment_no': institute_student.enrollment_no,
                'registration_no': institute_student.registration_no,
                'created_on': subject_student.created_on,
                'image': '',
                'active': subject_student.active
            }

            return Response(response, status=status.HTTP_201_CREATED)
        except IntegrityError:
            return Response({'error': _('Student was already invited.')},
                            status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            return Response({'error': _('Unknown error occurred.')},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class InstituteSubjectStudentListView(APIView):
    """
    View for getting active and inactive student list
    by permitted user (admin and class staff)
    """
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def get(self, *args, **kwargs):
        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug')
        ).only('name').first()

        if not institute:
            return Response({'error': _('Institute not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        class_ = models.InstituteClass.objects.filter(
            class_slug=kwargs.get('class_slug')
        ).only('name').first()

        if not class_:
            return Response({'error': _('Class not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug')
        ).only('name').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        has_subject_perm = False
        perm = models.InstitutePermission.objects.filter(
            invitee=self.request.user,
            institute__pk=institute.pk,
            active=True
        ).only('role').first()

        if not perm or perm.role != models.InstituteRole.ADMIN:
            if not models.InstituteSubjectPermission.objects.filter(
                invitee=self.request.user,
                to__pk=subject.pk
            ).exists():
                if not models.InstituteClassPermission.objects.filter(
                    invitee=self.request.user,
                    to__pk=class_.pk
                ).exists():
                    return Response({'error': _('Permission denied.')},
                                    status=status.HTTP_400_BAD_REQUEST)
            else:
                has_subject_perm = True

        if not has_subject_perm and models.InstituteSubjectPermission.objects.filter(
            invitee=self.request.user,
            to__pk=subject.pk
        ).exists():
            has_subject_perm = True

        order = get_active_common_license(institute)
        if not order:
            return Response({'error': _('License not found or expired.')},
                            status=status.HTTP_400_BAD_REQUEST)

        student_list = None
        if kwargs.get('student_type') == 'active':
            student_list = models.InstituteSubjectStudents.objects.filter(
                institute_subject__pk=subject.pk,
                active=True,
                is_banned=False
            ).only('institute_student', 'created_on', 'is_banned').order_by(
                'institute_student__enrollment_no')
        elif kwargs.get('student_type') == 'inactive':
            student_list = models.InstituteSubjectStudents.objects.filter(
                institute_subject__pk=subject.pk,
                active=False,
                is_banned=False
            ).only('institute_student', 'created_on', 'is_banned').order_by(
                'institute_student__enrollment_no')
        elif kwargs.get('student_type') == 'banned':
            student_list = models.InstituteSubjectStudents.objects.filter(
                institute_subject__pk=subject.pk,
                is_banned=True
            ).only('institute_student', 'created_on', 'active', 'is_banned').order_by(
                'institute_student__enrollment_no')
        response = list()

        for s in student_list:
            res = dict()
            res['invitee_email'] = str(s.institute_student.invitee)
            res['created_on'] = s.created_on
            res['is_banned'] = s.is_banned
            res['id'] = s.institute_student.pk
            res['first_name'] = s.institute_student.first_name
            res['last_name'] = s.institute_student.last_name
            res['gender'] = s.institute_student.gender
            res['date_of_birth'] = s.institute_student.date_of_birth
            res['enrollment_no'] = s.institute_student.enrollment_no
            res['registration_no'] = s.institute_student.registration_no
            res['image'] = ''

            if s.is_banned:
                ban_details = models.InstituteSubjectBannedStudent.objects.filter(
                    institute_student__invitee=s.institute_student.invitee,
                    banned_subject__pk=subject.pk
                ).first()
                res['banning_reason'] = ban_details.reason
                res['banned_on'] = ban_details.created_on
                res['ban_start_date'] = ban_details.start_date
                res['active'] = s.active

                if ban_details.banned_by.user_profile.first_name:
                    first_name = ban_details.banned_by.user_profile.first_name
                    last_name = ban_details.banned_by.user_profile.last_name
                    res['banned_by'] = first_name + ' ' + last_name
                else:
                    res['banned_by'] = str(ban_details.banned_by)

                if ban_details.end_date != models.UNLIMITED:
                    res['ban_end_date'] = ban_details.end_date

            response.append(res)

        return Response({
            'requester_role': perm.role,
            'has_perm': has_subject_perm,
            'data': response
        }, status=status.HTTP_200_OK)


class CreateInstituteView(CreateAPIView):
    """View for creating institute by teacher"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)
    serializer_class = serializer.CreateInstituteSerializer

    def get_serializer(self, *args, **kwargs):
        """
        Return the serializer instance that should be used for validating and
        deserializing input, and for serializing output.
        """
        serializer_class_ = self.get_serializer_class()
        context = self.get_serializer_context()
        context['user'] = self.request.user
        kwargs['context'] = context
        return serializer_class_(*args, **kwargs)

    def create(self, request, *args, **kwargs):
        """
        Overriding create method to send only slug field
        and created status
        """
        serializer_ = self.get_serializer(data=request.data)
        serializer_.is_valid(raise_exception=True)
        try:
            self.perform_create(serializer_)
            headers = self.get_success_headers(serializer_.data)
            return Response({
                'created': 'true',
                'url': serializer_.data['url']
            }, status=status.HTTP_201_CREATED, headers=headers)
        except Exception:
            return Response({
                'created': 'false',
                'message': _('You have already created an institute with this name.')
            }, status=status.HTTP_400_BAD_REQUEST)


class InstituteFullDetailsView(RetrieveAPIView):
    """View for getting full details of the institute"""
    authentication_classes = (TokenAuthentication, )
    permission_classes = (IsAuthenticated, IsTeacher)
    serializer_class = serializer.InstituteFullDetailsSerializer
    queryset = models.Institute.objects.all()
    lookup_field = 'institute_slug'

    def get_serializer(self, *args, **kwargs):
        """
        Return the serializer instance that should be used for validating and
        deserializing input, and for serializing output.
        """
        serializer_class = self.get_serializer_class()
        context = self.get_serializer_context()
        context['user'] = self.request.user
        kwargs['context'] = context
        return serializer_class(*args, **kwargs)


class InstituteProvidePermissionView(APIView):
    """View for providing permission to institute"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)
    serializer_class = serializer.InstituteProvidePermissionSerializer

    def post(self, request, *args, **kwargs):
        """Method to provide permission on post request"""
        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug')).first()

        if not institute:
            return Response({'error': _('Invalid institute.')},
                            status=status.HTTP_400_BAD_REQUEST)

        errors = {}
        role = request.data.get('role')
        invitee_email = request.data.get('invitee')
        invitee = None

        if not role:
            errors['role'] = _('This field is required.')
        elif role not in [models.InstituteRole.STAFF, models.InstituteRole.FACULTY, models.InstituteRole.ADMIN]:
            errors['role'] = _('Invalid role.')

        if not invitee_email:
            errors['invitee'] = _('This field is required.')
        else:
            invitee = get_user_model().objects.filter(
                email=invitee_email.lower().strip()).first()
            if not invitee:
                errors['invitee'] = _('This user does not exist.')
            elif not invitee.is_teacher:
                msg = _('Only teacher user can be assigned special roles.')
                errors['invitee'] = msg

        if errors:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)

        inviter = self.request.user
        inviter_perm = models.InstitutePermission.objects.filter(
            institute=institute,
            invitee=inviter,
            active=True
        ).first()
        license_ = get_active_common_license(institute)

        if not license_:
            return Response({'error': _('License expired or not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        stat = models.InstituteStatistics.objects.filter(institute=institute).first()

        if role == models.InstituteRole.STAFF:
            if stat.no_of_staffs >= license_.selected_license.no_of_staff:
                return Response({'error': _('Max no of staffs already invited.')},
                                status=status.HTTP_400_BAD_REQUEST)
        elif role == models.InstituteRole.FACULTY:
            if stat.no_of_faculties >= license_.selected_license.no_of_faculty:
                return Response({'error': _('Max no of faculties already invited.')},
                                status=status.HTTP_400_BAD_REQUEST)
        elif role == models.InstituteRole.ADMIN:
            if stat.no_of_admins >= license_.selected_license.no_of_admin:
                return Response({'error': _('Max no of admins already invited.')},
                                status=status.HTTP_400_BAD_REQUEST)

        # For assigning admin or staff role
        if role == models.InstituteRole.ADMIN or role == models.InstituteRole.STAFF:
            if not inviter_perm or \
                    inviter_perm.role != models.InstituteRole.ADMIN:
                return Response({'error': _('Insufficient permission.')},
                                status=status.HTTP_400_BAD_REQUEST)

            existing_invite = models.InstitutePermission.objects.filter(
                institute=institute,
                invitee=invitee
            ).first()

            if existing_invite and role == models.InstituteRole.ADMIN:
                if existing_invite.role == models.InstituteRole.ADMIN and not existing_invite.active:
                    return Response({'invitee': _('User already invited.')},
                                    status=status.HTTP_400_BAD_REQUEST)
                if existing_invite.role == models.InstituteRole.ADMIN and existing_invite.active:
                    msg = _('User already has admin permission.')
                    return Response({'invitee': msg},
                                    status=status.HTTP_400_BAD_REQUEST)
                elif existing_invite.role == models.InstituteRole.STAFF:
                    msg = _('Remove staff permission and try again.')
                    return Response({'invitee': msg},
                                    status=status.HTTP_400_BAD_REQUEST)
                elif existing_invite.role == models.InstituteRole.FACULTY:
                    msg = _('Remove faculty permission and try again.')
                    return Response({'invitee': msg},
                                    status=status.HTTP_400_BAD_REQUEST)
            elif existing_invite and role == models.InstituteRole.STAFF:
                if existing_invite.role == models.InstituteRole.STAFF and not existing_invite.active:
                    msg = _('User already invited.')
                    return Response({'invitee': msg},
                                    status=status.HTTP_400_BAD_REQUEST)
                elif existing_invite.role == models.InstituteRole.STAFF and existing_invite.active:
                    msg = _('User already has staff permission.')
                    return Response({'invitee': msg},
                                    status=status.HTTP_400_BAD_REQUEST)
                elif existing_invite.role == models.InstituteRole.ADMIN and existing_invite.active:
                    msg = _('Unauthorised. User is admin.')
                    return Response({'invitee': msg},
                                    status=status.HTTP_400_BAD_REQUEST)
                elif existing_invite.role == models.InstituteRole.ADMIN and not existing_invite.active:
                    msg = _('Unauthorised. User was requested to be admin.')
                    return Response({'invitee': msg},
                                    status=status.HTTP_400_BAD_REQUEST)
                elif existing_invite.role == models.InstituteRole.FACULTY:
                    msg = _('Remove faculty permission and try again.')
                    return Response({'invitee': msg},
                                    status=status.HTTP_400_BAD_REQUEST)

            ser = self.serializer_class(data={
                'inviter': inviter.pk,
                'invitee': invitee.pk,
                'institute': institute.pk,
                'role': role,
            })

            if ser.is_valid():
                try:
                    saved_data = ser.save()
                    response = {
                        'email': str(saved_data.invitee),
                        'image': None,
                        'invitation_id': saved_data.pk,
                        'invitee_id': saved_data.invitee.pk,
                        'inviter': str(self.request.user),
                        'requested_on': saved_data.request_date
                    }
                    if role == models.InstituteRole.ADMIN:
                        stat.no_of_admins += 1
                    else:
                        stat.no_of_staffs += 1
                    stat.save()
                    return Response(response, status=status.HTTP_200_OK)
                except Exception:
                    msg = _('Internal server error. Please contact Eduweb')
                    return Response({'error': msg},
                                    status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            else:
                return Response(ser.errors, status=status.HTTP_400_BAD_REQUEST)

        # For assigning faculty role
        elif role == models.InstituteRole.FACULTY:
            if not inviter_perm or \
                    inviter_perm.role == models.InstituteRole.FACULTY:
                return Response({'error': _('Insufficient permission.')},
                                status=status.HTTP_400_BAD_REQUEST)

            existing_invite = models.InstitutePermission.objects.filter(
                institute=institute,
                invitee=invitee
            ).first()

            if existing_invite:
                if existing_invite.role == models.InstituteRole.FACULTY and not existing_invite.active:
                    return Response({'invitee': _('User already invited.')},
                                    status=status.HTTP_400_BAD_REQUEST)
                elif existing_invite.role == models.InstituteRole.FACULTY and existing_invite.active:
                    return Response({'invitee': _('User is already a faculty.')},
                                    status=status.HTTP_400_BAD_REQUEST)
                elif existing_invite.role == models.InstituteRole.ADMIN and not existing_invite.active:
                    msg = _('Unauthorized. User was already requested for admin role.')
                    return Response({'invitee': msg},
                                    status=status.HTTP_400_BAD_REQUEST)
                elif existing_invite.role == models.InstituteRole.ADMIN and existing_invite.active:
                    msg = _('Unauthorized. User has admin permissions.')
                    return Response({'invitee': msg},
                                    status=status.HTTP_400_BAD_REQUEST)
                elif existing_invite.role == models.InstituteRole.STAFF and not existing_invite.active:
                    msg = _('Unauthorized. User was already requested for staff role.')
                    return Response({'invitee': msg},
                                    status=status.HTTP_400_BAD_REQUEST)
                elif existing_invite.role == models.InstituteRole.STAFF and existing_invite.active:
                    msg = _('Unauthorized. User has staff permissions.')
                    return Response({'invitee': msg},
                                    status=status.HTTP_400_BAD_REQUEST)

            ser = self.serializer_class(data={
                'inviter': inviter.pk,
                'invitee': invitee.pk,
                'institute': institute.pk,
                'role': role,
                'request_accepted_on': timezone.now()
            })

            if ser.is_valid():
                try:
                    saved_data = ser.save()
                    response = {
                        'email': str(saved_data.invitee),
                        'image': None,
                        'invitation_id': saved_data.pk,
                        'invitee_id': saved_data.invitee.pk,
                        'inviter': str(self.request.user),
                        'requested_on': saved_data.request_date
                    }
                    stat.no_of_faculties += 1
                    stat.save()
                    return Response(response, status=status.HTTP_200_OK)
                except Exception:
                    msg = _('Internal server error. Please contact Eduweb')
                    return Response({'error': msg},
                                    status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            else:
                return Response(ser.errors, status=status.HTTP_400_BAD_REQUEST)


class InstitutePermissionAcceptDeleteView(APIView):
    """View for accepting or deleting permission"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def post(self, request, *args, **kwargs):
        """Method to accept or delete permission on post request"""
        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug')
        ).first()

        if not institute:
            return Response({'error': _('Invalid institute.')},
                            status=status.HTTP_400_BAD_REQUEST)

        errors = {}
        operation = request.data.get('operation')

        if not operation:
            errors['operation'] = _('This field is required.')
        elif operation.upper() != 'ACCEPT' and operation.upper() != 'DELETE':
            errors['operation'] = _('Invalid operation.')

        if errors:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)

        if operation.upper() == 'ACCEPT':
            invitee = self.request.user
            invitation = models.InstitutePermission.objects.filter(
                institute=institute,
                invitee=invitee
            ).first()

            if not invitation:
                return Response({'error': _('Invitation not found or already deleted.')},
                                status=status.HTTP_400_BAD_REQUEST)
            elif invitation.active:
                msg = _('Join request already accepted.')
                return Response({'error': msg},
                                status=status.HTTP_400_BAD_REQUEST)

            invitation.active = True
            invitation.request_accepted_on = timezone.now()
            try:
                invitation.save()
                return Response({'status': 'ACCEPTED'},
                                status=status.HTTP_200_OK)
            except Exception:
                msg = _('Internal server error. Please contact EduWeb.')
                return Response({'error': msg},
                                status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        elif operation.upper() == 'DELETE':
            invitee_email = request.data.get('invitee')

            # Inviter or admin is trying to delete join request
            if invitee_email:
                invitee = get_user_model().objects.filter(
                    email=invitee_email
                ).first()

                if not invitee:
                    msg = _('This user does not exist.')
                    return Response({'invitee': msg},
                                    status=status.HTTP_400_BAD_REQUEST)

                invitation = models.InstitutePermission.objects.filter(
                    institute=institute,
                    invitee=invitee
                ).first()

                if not invitation:
                    msg = _('Invitation not found or already deleted.')
                    return Response({'error': msg},
                                    status=status.HTTP_400_BAD_REQUEST)

                inviter = models.InstitutePermission.objects.filter(
                    institute=institute,
                    invitee=self.request.user,
                    active=True
                ).first()

                if not inviter:
                    return Response({'error': 'Permission denied.'},
                                    status=status.HTTP_400_BAD_REQUEST)
                # Active user can not be deleted using this url.
                elif invitation.active and invitation.role != inviter.role:
                    msg = _('Internal server error. Please contact EduWeb.')
                    return Response({'error': msg},
                                    status=status.HTTP_500_INTERNAL_SERVER_ERROR)

                # Same role can not delete same role permission
                # Admin can delete staff and faculty permission
                # Staff can only add faculty but not delete
                # Faculty can neither add nor delete
                if inviter.role != models.InstituteRole.ADMIN or\
                        invitation.active and inviter.role == invitation.role:
                    return Response({'error': _('Permission denied.')},
                                    status=status.HTTP_400_BAD_REQUEST)

                try:
                    role = invitation.role
                    invitation.delete()
                    stat = models.InstituteStatistics.objects.filter(
                        institute=institute
                    ).first()
                    if role == models.InstituteRole.FACULTY:
                        stat.no_of_faculties -= 1
                    elif role == models.InstituteRole.STAFF:
                        stat.no_of_staffs -= 1
                    elif role == models.InstituteRole.ADMIN:
                        stat.no_of_admins -= 1
                    stat.save()
                    return Response({'status': 'DELETED'},
                                    status=status.HTTP_200_OK)
                except Exception:
                    msg = _('Internal Server Error. Please contact EduWeb.')
                    return Response({'error': msg},
                                    status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            # Invitee is trying to delete join request
            else:
                invitation = models.InstitutePermission.objects.filter(
                    institute=institute,
                    invitee=self.request.user
                ).first()

                if not invitation:
                    msg = _('Invitation not found or already deleted.')
                    return Response({'error': msg},
                                    status=status.HTTP_400_BAD_REQUEST)

                # Deleting invitation
                try:
                    role = invitation.role
                    invitation.delete()
                    stat = models.InstituteStatistics.objects.filter(
                        institute=institute
                    ).first()
                    if role == models.InstituteRole.FACULTY:
                        stat.no_of_faculties -= 1
                    elif role == models.InstituteRole.STAFF:
                        stat.no_of_staffs -= 1
                    elif role == models.InstituteRole.ADMIN:
                        stat.no_of_admins -= 1
                    stat.save()
                    return Response({'status': 'DELETED'},
                                    status=status.HTTP_200_OK)
                except Exception:
                    msg = _('Internal server error occurred. Please contact EduWeb')
                    return Response({'error': msg},
                                    status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class InstitutePermittedUserListView(APIView):
    """View to get permitted user list"""
    authentication_classes = (TokenAuthentication, )
    permission_classes = (IsAuthenticated, IsTeacher, )

    def _format_data(self, user_invites, active=True):
        """Return list of user details in list"""
        list_user_data = []
        for invite in user_invites:
            user_data = dict()
            user_data['invitation_id'] = invite.pk
            user_data['invitee_id'] = invite.invitee.pk
            user_data['email'] = str(invite.invitee)
            user_data['inviter'] = str(invite.inviter)
            user_data['image'] = None
            if active:
                user_data['request_accepted_on'] = str(invite.request_accepted_on)
            else:
                user_data['requested_on'] = str(invite.request_date)
            list_user_data.append(user_data)
        return list_user_data

    def get(self, *args, **kwargs):
        """Post request to get permitted user of institute"""
        errors = {}
        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug')
        ).first()
        if not institute:
            errors['institute_slug'] = 'Invalid'

        role = kwargs.get('role').upper()
        if role != 'ADMIN' and role != 'STAFF' and role != 'FACULTY':
            errors['role'] = 'Invalid'

        if errors:
            return Response({'error': _('Invalid credentials.')},
                            status=status.HTTP_400_BAD_REQUEST)

        has_perm = models.InstitutePermission.objects.filter(
            institute=institute,
            invitee=self.request.user,
            active=True
        ).exists()

        if not has_perm:
            return Response({'error': _('Permission denied.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if role == 'ADMIN':
            permitted_user_invitations = models.InstitutePermission.objects.filter(
                institute=institute,
                role=models.InstituteRole.ADMIN
            )
            response_data = {
                'active_admin_list': self._format_data(
                    permitted_user_invitations.filter(active=True)),
                'pending_admin_invites': self._format_data(
                    permitted_user_invitations.filter(active=False), False)
            }
            return Response(response_data, status=status.HTTP_200_OK)
        elif role == 'STAFF':
            permitted_user_invitations = models.InstitutePermission.objects.filter(
                institute=institute,
                role=models.InstituteRole.STAFF
            )
            response_data = {
                'active_staff_list': self._format_data(
                    permitted_user_invitations.filter(active=True)),
                'pending_staff_invites': self._format_data(
                    permitted_user_invitations.filter(active=False), False)
            }
            return Response(response_data, status=status.HTTP_200_OK)
        elif role == 'FACULTY':
            permitted_user_invitations = models.InstitutePermission.objects.filter(
                institute=institute,
                role=models.InstituteRole.FACULTY
            )
            response_data = {
                'active_faculty_list': self._format_data(
                    permitted_user_invitations.filter(active=True)),
                'pending_faculty_invites': self._format_data(
                    permitted_user_invitations.filter(active=False), False)
            }
            return Response(response_data, status=status.HTTP_200_OK)


class CreateClassView(CreateAPIView):
    """View to creating institute class"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher,)
    serializer_class = serializer.InstituteClassSerializer

    def create(self, request, *args, **kwargs):
        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug')
        ).first()

        if not institute:
            return Response({'error': _('Institute not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstitutePermission.objects.filter(
            institute=institute,
            invitee=self.request.user,
            active=True,
            role=models.InstituteRole.ADMIN
        ).exists():
            return Response({'error': _('Permission denied.')},
                            status=status.HTTP_400_BAD_REQUEST)

        license_ = get_active_common_license(institute)

        if not license_:
            return Response({'error': _('License expired or not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        ins_stat = models.InstituteStatistics.objects.filter(institute=institute).first()
        if ins_stat.class_count >= license_.selected_license.classroom_limit:
            return Response({'error': _('Maximum class creation limit attained.')},
                            status=status.HTTP_400_BAD_REQUEST)

        serializer_ = self.get_serializer(data={
            'class_institute': institute.pk,
            'name': request.data.get('name')
        })
        if serializer_.is_valid():
            serializer_.save()
            ins_stat.class_count += 1
            ins_stat.save()
            response = serializer_.data
            response['class_incharges'] = list()
            return Response(response, status=status.HTTP_201_CREATED)
        else:
            return Response(serializer_.errors, status=status.HTTP_400_BAD_REQUEST)


class DeleteClassView(DestroyAPIView):
    """View for deleting class"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher,)

    def destroy(self, request, *args, **kwargs):
        """Only active admin or permitted staff can delete"""
        class_ = models.InstituteClass.objects.filter(
            class_slug=kwargs.get('class_slug')
        ).first()

        if not class_:
            return Response(status=status.HTTP_204_NO_CONTENT)

        institute = models.Institute.objects.filter(
            pk=class_.class_institute.pk).first()

        if institute:
            permission = models.InstitutePermission.objects.filter(
                institute=institute,
                invitee=self.request.user,
                active=True
            ).first()

            if not permission or\
                    permission.role == models.InstituteRole.FACULTY or\
                    permission.role == models.InstituteRole.STAFF and\
                    not models.InstituteClassPermission.objects.filter(
                        invitee=self.request.user, to=class_
                    ).exists():
                return Response({'error': 'Permission denied.'},
                                status=status.HTTP_400_BAD_REQUEST)
            class_.delete()
            stat = models.InstituteStatistics.objects.filter(
                institute=institute
            ).first()
            stat.class_count -= 1
            stat.save()
            return Response(status=status.HTTP_204_NO_CONTENT)
        else:
            return Response({'error': 'Institute not found.'},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ListSlugNamePairsView(APIView):
    """View for getting the list of class slug and class names"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher,)

    def get(self, *args, **kwargs):
        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug')
        ).first()

        if not institute:
            return Response({'error': _('Invalid Institute.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstitutePermission.objects.filter(
            institute=institute,
            invitee=self.request.user,
            active=True
        ).exists():
            return Response({'error': _('Permission denied.')},
                            status=status.HTTP_400_BAD_REQUEST)

        class_list = models.InstituteClass.objects.filter(
            class_institute=institute,
        ).order_by('created_on')
        response = list()

        for c in class_list:
            response.append({
                'class_slug': c.class_slug,
                'name': c.name
            })

        return Response(response, status.HTTP_200_OK)


class ListAllClassView(APIView):
    """View for listing all classes"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher,)

    def get(self, *args, **kwargs):
        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug')
        ).first()

        if not institute:
            return Response({'error': _('Invalid Institute.')},
                            status=status.HTTP_400_BAD_REQUEST)

        perm = models.InstitutePermission.objects.filter(
            institute=institute,
            invitee=self.request.user,
            active=True
        ).first()

        if not perm:
            return Response({'error': _('Permission denied.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not get_active_common_license(institute):
            return Response({'error': _('License expired or not purchased.')},
                            status=status.HTTP_400_BAD_REQUEST)

        queryset = models.InstituteClass.objects.filter(
            class_institute=institute
        ).order_by('created_on')
        response = []

        for data in queryset:
            class_details = dict()
            class_details['id'] = data.id
            class_details['name'] = data.name
            class_details['class_slug'] = data.class_slug
            class_details['created_on'] = data.created_on
            class_ = models.InstituteClass.objects.filter(
                class_slug=data.class_slug).first()
            if perm and perm.role == models.InstituteRole.ADMIN or\
                    models.InstituteClassPermission.objects.filter(
                        invitee=self.request.user, to=class_).exists():
                class_details['has_class_perm'] = True
            else:
                class_details['has_class_perm'] = False

            class_incharges = list()
            incharges = models.InstituteClassPermission.objects.filter(
                to=class_
            ).order_by('created_on')

            for incharge in incharges:
                incharge_details = dict()
                incharge_details['id'] = incharge.invitee.pk
                incharge_details['email'] = str(incharge.invitee)
                invitee = get_user_model().objects.filter(pk=incharge.invitee.pk).first()
                invitee = models.UserProfile.objects.filter(
                    user=invitee
                ).first()
                incharge_details['name'] = invitee.first_name + ' ' + invitee.last_name
                class_incharges.append(incharge_details)

            class_details['class_incharges'] = class_incharges
            response.append(class_details)

        return Response(response, status=status.HTTP_200_OK)


class ProvideClassPermissionView(CreateAPIView):
    """View for providing class permission by admin to staff/admin"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher,)

    def create(self, request, *args, **kwargs):
        class_ = models.InstituteClass.objects.filter(
            class_slug=request.data.get('class_slug')
        ).first()

        if not class_:
            return Response({'error': _('Class not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        institute = models.Institute.objects.filter(
            pk=class_.class_institute.pk).first()

        if not models.InstitutePermission.objects.filter(
            institute=institute,
            active=True,
            invitee=self.request.user,
            role=models.InstituteRole.ADMIN
        ).exists():
            return Response({'error': _('Permission denied.')},
                            status=status.HTTP_400_BAD_REQUEST)

        invitee = get_user_model().objects.filter(
            email=request.data.get('invitee')
        ).first()

        if not invitee:
            return Response({'error': _('This user does not exist.')},
                            status=status.HTTP_400_BAD_REQUEST)

        invitee_perm = models.InstitutePermission.objects.filter(
            institute=institute,
            active=True,
            invitee=invitee
        ).first()

        if not invitee_perm:
            return Response({'error': _('User is not a member of this institute.')},
                            status=status.HTTP_400_BAD_REQUEST)
        elif invitee_perm.role == models.InstituteRole.FACULTY:
            return Response({'error': _('Faculty can not be provided class permission.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if models.InstituteClassPermission.objects.filter(
                to=class_,
                invitee=invitee).exists():
            return Response({'error': _('User already has class permission.')},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            perm = models.InstituteClassPermission.objects.create(
                invitee=invitee,
                inviter=self.request.user,
                to=class_
            )
            invitee = get_object_or_404(models.UserProfile,
                                        user=invitee)
            inviter = get_object_or_404(models.UserProfile,
                                        user=self.request.user)
            return Response({
                'id': perm.id,
                'invitee_id': perm.invitee.pk,
                'name': invitee.first_name + ' ' + invitee.last_name,
                'email': str(invitee),
                'inviter_name': inviter.first_name + ' ' + inviter.last_name,
                'inviter_email': str(inviter),
                'created_on': perm.created_on,
                'image': None
            }, status=status.HTTP_201_CREATED)
        except IntegrityError:
            return Response({'error': _('User is already class incharge.')},
                            status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            return Response({'error': _('Internal server error.')},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ListPermittedClassInchargeView(APIView):
    """View for listing all permitted class incharges"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher,)

    def get(self, *args, **kwargs):
        class_ = models.InstituteClass.objects.filter(
            class_slug=kwargs.get('class_slug')
        ).first()

        if not class_:
            return Response({'error': _('Class not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstitutePermission.objects.filter(
            institute=models.Institute.objects.filter(
                pk=class_.class_institute.pk).first(),
            active=True,
            invitee=self.request.user
        ).exists():
            return Response({'error': _('Permission denied.')},
                            status=status.HTTP_400_BAD_REQUEST)

        response = []
        perm = models.InstituteClassPermission.objects.filter(
            to=class_
        ).order_by('created_on')
        for p in perm:
            res = dict()
            invitee = models.UserProfile.objects.filter(
                user=get_user_model().objects.filter(
                    pk=p.invitee.pk).first()).first()
            inviter = None
            if p.inviter:
                inviter = models.UserProfile.objects.filter(
                    user=get_user_model().objects.filter(
                        pk=p.inviter.pk).first()).first()
            res['id'] = p.id
            res['invitee_id'] = p.invitee.pk
            res['name'] = invitee.first_name + ' ' + invitee.last_name
            res['email'] = str(p.invitee)
            if inviter:
                res['inviter_name'] = inviter.first_name + ' ' + inviter.last_name
                res['inviter_email'] = str(p.inviter)
            else:
                res['inviter_name'] = 'Anonymous'
                res['inviter_email'] = ' '
            res['created_on'] = p.created_on
            res['image'] = None
            response.append(res)
        return Response(response, status=status.HTTP_200_OK)


class CheckClassPermView(APIView):
    """View returns true if user has class perm"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def get(self, *args, **kwargs):
        class_ = models.InstituteClass.objects.filter(
            class_slug=kwargs.get('class_slug')
        ).first()

        if not class_:
            return Response({'error': _('Class not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        perm = models.InstitutePermission.objects.filter(
            institute=models.Institute.objects.filter(
                pk=class_.class_institute.pk).first(),
            invitee=self.request.user,
            active=True
        ).first()

        if not perm:
            return Response({'error': _('Permission denied.')},
                            status=status.HTTP_400_BAD_REQUEST)
        elif perm.role == models.InstituteRole.FACULTY:
            return Response({'status': False},
                            status=status.HTTP_200_OK)
        elif perm.role == models.InstituteRole.ADMIN:
            return Response({'status': True},
                            status=status.HTTP_200_OK)
        else:
            if models.InstituteClassPermission.objects.filter(
                to=class_,
                invitee=self.request.user
            ).exists():
                return Response({'status': True},
                                status=status.HTTP_200_OK)
            else:
                return Response({'status': False},
                                status=status.HTTP_200_OK)


class CreateSubjectView(APIView):
    """View for creating subject"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def post(self, request, *args, **kwargs):
        class_ = models.InstituteClass.objects.filter(
            class_slug=kwargs.get('class_slug')
        ).first()

        if not class_:
            return Response({'error': _('Class not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not request.data.get('name'):
            return Response({'error': _('Subject name is required.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not request.data.get('type'):
            return Response({'error': _('Subject type is required.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not request.data.get('name').strip():
            return Response({'error': _('Subject name can not be blank.')},
                            status=status.HTTP_400_BAD_REQUEST)

        has_perm = models.InstituteClassPermission.objects.filter(
            to=class_,
            invitee=self.request.user
        ).exists()

        if not has_perm:
            if not models.InstitutePermission.objects.filter(
                institute=models.Institute.objects.filter(
                    pk=class_.class_institute.pk).first(),
                invitee=self.request.user,
                active=True,
                role=models.InstituteRole.ADMIN
            ).exists():
                return Response({'error': _('Permission denied.')},
                                status=status.HTTP_400_BAD_REQUEST)

        try:
            subject = models.InstituteSubject.objects.create(
                subject_class=class_,
                name=self.request.data.get('name'),
                type=self.request.data.get('type')
            )
            return Response({
                'id': subject.id,
                'name': subject.name,
                'type': subject.type,
                'subject_slug': subject.subject_slug,
                'created_on': subject.created_on,
                'subject_incharges': []
            }, status=status.HTTP_201_CREATED)
        except IntegrityError:
            return Response({'error': _('Subject with same name exists.')},
                            status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            return Response({'error': _('Internal server error.')},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ListAllSubjectView(APIView):
    """View for listing all subjects"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def get(self, *args, **kwargs):
        class_ = models.InstituteClass.objects.filter(
            class_slug=kwargs.get('class_slug')
        ).only('class_slug').first()

        if not class_:
            return Response({'error': _('Class not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        institute = models.Institute.objects.filter(
                pk=class_.class_institute.pk).only('institute_slug').first()
        perm = models.InstitutePermission.objects.filter(
            institute=institute,
            invitee=self.request.user,
            active=True
        ).only('role').first()

        if not perm:
            return Response({'error': _('Permission denied.')},
                            status=status.HTTP_400_BAD_REQUEST)

        subject_list = models.InstituteSubject.objects.filter(
            subject_class=class_
        ).filter().order_by('created_on')
        response = []
        for sub in subject_list:
            subject_details = dict()
            subject_details['id'] = sub.id
            subject_details['name'] = sub.name
            subject_details['subject_slug'] = sub.subject_slug
            subject_details['type'] = sub.type
            subject_details['created_on'] = sub.created_on
            if perm and perm.role == models.InstituteRole.ADMIN:
                subject_details['has_subject_perm'] = True
            else:
                institute_subject = models.InstituteSubject.objects.filter(
                    subject_slug=sub.subject_slug).first()
                if models.InstituteSubjectPermission.objects.filter(
                    to=institute_subject,
                    invitee=self.request.user
                ).exists():
                    subject_details['has_subject_perm'] = True
                else:
                    subject_details['has_subject_perm'] = False
            subject_incharges = list()
            incharges = models.InstituteSubjectPermission.objects.filter(
                to=sub
            ).order_by('created_on')

            for perm_ in incharges:
                incharge_details = dict()
                incharge_details['id'] = perm_.invitee.pk
                incharge_details['email'] = str(perm_.invitee)
                invitee = get_user_model().objects.filter(
                    pk=perm_.invitee.pk).first()
                invitee = models.UserProfile.objects.filter(
                    user=invitee).first()
                incharge_details['name'] = invitee.first_name + ' ' + invitee.last_name
                subject_incharges.append(incharge_details)

            subject_details['subject_incharges'] = subject_incharges
            response.append(subject_details)
        return Response(response, status=status.HTTP_200_OK)


class ListSubjectInstructorsView(APIView):
    """View for listing all class instructors"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def get(self, *args, **kwargs):
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug')
        ).first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        class_ = models.InstituteClass.objects.get(
            pk=subject.subject_class.pk)
        institute = models.Institute.objects.get(
            pk=class_.class_institute.pk)

        if not models.InstitutePermission.objects.filter(
            institute=institute,
            invitee=self.request.user,
            active=True
        ).exists():
            return Response({'error': _('Permission denied.')},
                            status=status.HTTP_400_BAD_REQUEST)

        perm_list = models.InstituteSubjectPermission.objects.filter(
            to=subject
        ).order_by('created_on')
        response = []

        for perm in perm_list:
            invite_details = dict()
            invite_details['id'] = perm.id
            invite_details['invitee_id'] = perm.invitee.pk
            invite_details['email'] = str(perm.invitee)
            invitee = get_user_model().objects.filter(
                pk=perm.invitee.pk).first()
            invitee = models.UserProfile.objects.filter(
                user=invitee).first()

            if perm.inviter:
                inviter = get_user_model().objects.filter(
                    pk=perm.inviter.pk).first()
                inviter = models.UserProfile.objects.filter(
                    user=inviter).first()
                invite_details['inviter_name'] = inviter.first_name + ' ' + inviter.last_name
                invite_details['inviter_email'] = str(perm.inviter)
            else:
                invite_details['inviter_name'] = ' '
                invite_details['inviter_email'] = ' '

            invite_details['name'] = invitee.first_name + ' ' + invitee.last_name
            invite_details['created_on'] = perm.created_on
            invite_details['image'] = None
            response.append(invite_details)

        return Response(response, status=status.HTTP_200_OK)


class AddSubjectPermissionView(APIView):
    """View for adding subject permission"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def post(self, request, *args, **kwargs):
        subject = models.InstituteSubject.objects.filter(
            subject_slug=request.data.get('subject_slug')
        ).first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        class_ = models.InstituteClass.objects.filter(
            pk=subject.subject_class.pk).first()
        institute = models.Institute.objects.filter(
            pk=class_.class_institute.pk).first()
        has_perm = models.InstituteClassPermission.objects.filter(
            to=class_,
            invitee=self.request.user
        ).exists()

        if not has_perm:
            if not models.InstitutePermission.objects.filter(
                institute=institute,
                invitee=self.request.user,
                active=True,
                role=models.InstituteRole.ADMIN
            ).exists():
                return Response({'error': _('Permission denied.')},
                                status=status.HTTP_400_BAD_REQUEST)

        invitee = get_user_model().objects.filter(
            email=request.data.get('invitee')
        ).first()

        if not invitee:
            return Response({'error': _('This user does not exist.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstitutePermission.objects.filter(
            institute=institute,
            invitee=invitee,
            active=True
        ).exists():
            return Response({'error': _('User is not a member of this institute.')},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            perm = models.InstituteSubjectPermission.objects.create(
                to=subject,
                inviter=self.request.user,
                invitee=invitee
            )
            invitee = models.UserProfile.objects.filter(user=invitee).first()
            inviter = models.UserProfile.objects.filter(user=self.request.user).first()
            return Response({
                'email': str(perm.invitee),
                'name': invitee.first_name + ' ' + invitee.last_name,
                'invitee_id': perm.invitee.pk,
                'inviter_email': str(perm.inviter),
                'inviter_name': inviter.first_name + ' ' + inviter.last_name,
                'created_on': perm.created_on,
                'image': None
            }, status=status.HTTP_201_CREATED)
        except IntegrityError:
            return Response({'error': _('User is already an instructor.')},
                            status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            return Response({'error': _('Internal Server Error.')},
                            status=status.HTTP_400_BAD_REQUEST)


class CreateSectionView(APIView):
    """View for creating section"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def post(self, request, *args, **kwargs):
        class_ = models.InstituteClass.objects.filter(
            class_slug=kwargs.get('class_slug')
        ).first()

        if not class_:
            return Response({'error': _('Class not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not request.data.get('name'):
            return Response({'error': _('Section name is required.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not request.data.get('name').strip():
            return Response({'error': _('Section name can not be blank.')},
                            status=status.HTTP_400_BAD_REQUEST)

        has_perm = models.InstituteClassPermission.objects.filter(
            to=class_,
            invitee=self.request.user
        ).exists()

        if not has_perm:
            institute = models.Institute.objects.filter(
                pk=class_.class_institute.pk).first()
            if not models.InstitutePermission.objects.filter(
                institute=institute,
                invitee=self.request.user,
                active=True,
                role=models.InstituteRole.ADMIN
            ).exists():
                return Response({'error': _('Permission denied.')},
                                status=status.HTTP_400_BAD_REQUEST)

        try:
            section = models.InstituteSection.objects.create(
                section_class=class_,
                name=self.request.data.get('name')
            )
            return Response({
                'name': section.name,
                'section_slug': section.section_slug,
                'created_on': section.created_on,
                'section_incharges': []
            }, status=status.HTTP_201_CREATED)
        except IntegrityError:
            return Response({'error': _('Section with same name exists.')},
                            status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            return Response({'error': _('Internal server error.')},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class AddSectionPermissionView(APIView):
    """View for adding section permission"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def post(self, request, *args, **kwargs):
        section = models.InstituteSection.objects.filter(
            section_slug=request.data.get('section_slug')
        ).first()

        if not section:
            return Response({'error': _('Section not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        class_ = models.InstituteClass.objects.filter(
            pk=section.section_class.pk).first()
        institute = models.Institute.objects.filter(
            pk=class_.class_institute.pk).first()
        has_perm = models.InstituteClassPermission.objects.filter(
            to=class_,
            invitee=self.request.user
        ).exists()

        if not has_perm:
            if not models.InstitutePermission.objects.filter(
                institute=institute,
                invitee=self.request.user,
                active=True,
                role=models.InstituteRole.ADMIN
            ).exists():
                return Response({'error': _('Permission denied.')},
                                status=status.HTTP_400_BAD_REQUEST)

        invitee = get_user_model().objects.filter(
            email=request.data.get('invitee')
        ).first()

        if not invitee:
            return Response({'error': _('This user does not exist.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstitutePermission.objects.filter(
            institute=institute,
            invitee=invitee,
            active=True
        ).exists():
            return Response({'error': _('User is not a member of this institute.')},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            perm = models.InstituteSectionPermission.objects.create(
                to=section,
                inviter=self.request.user,
                invitee=invitee
            )
            invitee = models.UserProfile.objects.filter(user=invitee).first()
            inviter = models.UserProfile.objects.filter(user=self.request.user).first()
            return Response({
                'email': str(perm.invitee),
                'name': invitee.first_name + ' ' + invitee.last_name,
                'invitee_id': perm.invitee.pk,
                'inviter_email': str(perm.inviter),
                'inviter_name': inviter.first_name + ' ' + inviter.last_name,
                'created_on': perm.created_on,
                'image': None
            }, status=status.HTTP_201_CREATED)
        except IntegrityError:
            return Response({'error': _('User already has section permission.')},
                            status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            return Response({'error': _('Internal Server Error.')},
                            status=status.HTTP_400_BAD_REQUEST)


class ListAllSectionView(APIView):
    """View for listing all section"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def get(self, *args, **kwargs):
        class_ = models.InstituteClass.objects.filter(
            class_slug=kwargs.get('class_slug')
        ).first()

        if not class_:
            return Response({'error': _('Class not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        perm = models.InstitutePermission.objects.filter(
            institute=models.Institute.objects.filter(
                pk=class_.class_institute.pk).first(),
            invitee=self.request.user,
            active=True
        ).first()

        if not perm:
            return Response({'error': _('Permission denied.')},
                            status=status.HTTP_400_BAD_REQUEST)

        section_list = models.InstituteSection.objects.filter(
            section_class=class_
        ).filter().order_by('created_on')
        response = []
        for section in section_list:
            section_details = dict()
            section_details['id'] = section.id
            section_details['name'] = section.name
            section_details['section_slug'] = section.section_slug
            section_details['created_on'] = section.created_on
            sec = models.InstituteSection.objects.filter(
                section_slug=section.section_slug).first()

            if perm and perm.role == models.InstituteRole.ADMIN:
                section_details['has_section_perm'] = True
            else:
                if models.InstituteSectionPermission.objects.filter(
                    to=sec,
                    invitee=self.request.user
                ).exists():
                    section_details['has_section_perm'] = True
                else:
                    section_details['has_section_perm'] = False

            section_incharges = list()
            invites = models.InstituteSectionPermission.objects.filter(
                to=sec
            )

            for invite in invites:
                incharge_details = dict()
                invitee = get_user_model().objects.filter(pk=invite.invitee.pk).first()
                invitee = models.UserProfile.objects.filter(user=invitee).first()
                incharge_details['id'] = invite.invitee.pk
                incharge_details['email'] = str(invite.invitee)
                incharge_details['name'] = invitee.first_name + ' ' + invitee.last_name
                section_incharges.append(incharge_details)

            section_details['section_incharges'] = section_incharges
            response.append(section_details)
        return Response(response, status=status.HTTP_200_OK)


class ListSectionInchargesView(APIView):
    """View for listing all section incharges"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def get(self, *args, **kwargs):
        section = models.InstituteSection.objects.filter(
            section_slug=kwargs.get('section_slug')
        ).first()

        if not section:
            return Response({'error': _('Section not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        class_ = models.InstituteClass.objects.get(pk=section.section_class.pk)
        institute = models.Institute.objects.get(pk=class_.class_institute.pk)

        if not models.InstitutePermission.objects.filter(
            institute=institute,
            invitee=self.request.user,
            active=True
        ).exists():
            return Response({'error': _('Permission denied.')},
                            status=status.HTTP_400_BAD_REQUEST)

        perm_list = models.InstituteSectionPermission.objects.filter(
            to=section
        ).order_by('created_on')
        response = []

        for perm in perm_list:
            invite_details = dict()
            invite_details['id'] = perm.id
            invite_details['email'] = str(perm.invitee)
            invite_details['invitee_id'] = perm.invitee.pk
            invitee = get_user_model().objects.filter(pk=perm.invitee.pk).first()
            invitee = models.UserProfile.objects.filter(user=invitee).first()

            if perm.inviter:
                inviter = get_user_model().objects.filter(pk=perm.inviter.pk).first()
                inviter = models.UserProfile.objects.filter(user=inviter).first()
                invite_details['inviter_name'] = inviter.first_name + ' ' + inviter.last_name
                invite_details['inviter_email'] = str(perm.inviter)
            else:
                invite_details['inviter_name'] = ' '
                invite_details['inviter_email'] = ' '

            invite_details['name'] = invitee.first_name + ' ' + invitee.last_name
            invite_details['created_on'] = perm.created_on
            invite_details['image'] = None
            response.append(invite_details)

        return Response(response, status=status.HTTP_200_OK)


class InstituteSubjectAddModuleView(APIView):
    """View for adding subject module or test"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def post(self, request, *args, **kwargs):
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug').lower()
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectPermission.objects.filter(
            to=subject,
            invitee=self.request.user
        ).exists():
            return Response({'error': _('Permission denied.')},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            view = models.SubjectViewNames.objects.create(
                view_subject=subject,
                name=request.data.get('name'),
                type=request.data.get('type')
            )
            return Response({
                'name': view.name,
                'view': view.key,
                'count': 0,
                'type': models.SubjectViewType.MODULE_VIEW
            }, status=status.HTTP_201_CREATED)
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except IntegrityError as e:
            if 'unique_key_for_subject_constraint' in str(e):
                return Response({'error': _('Please try again. If same error persists then report us.')},
                                status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            else:
                return Response({'error': _('Unknown error occurred.')},
                                status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        except Exception:
            return Response({'error': 'Unable to create module.'},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class InstituteSubjectMinStatisticsView(APIView):
    """View for getting subject course content min statistics"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def get(self, *args, **kwargs):
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug')
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        institute = models.Institute.objects.filter(
            pk=subject.subject_class.class_institute.pk
        ).only('institute_slug').first()

        has_subject_perm = False
        if models.InstituteSubjectPermission.objects.filter(
            to=subject,
            invitee=self.request.user
        ).exists():
            has_subject_perm = True
        else:
            if not models.InstitutePermission.objects.filter(
                institute=institute,
                role=models.InstituteRole.ADMIN,
                invitee=self.request.user,
                active=True
            ).exists():
                return Response({'error': _('Permission denied.')},
                                status=status.HTTP_400_BAD_REQUEST)

        order = get_active_common_license(institute)

        if not order:
            return Response({'error': _('License expired or not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        response = dict()
        views = models.SubjectViewNames.objects.filter(
            view_subject=subject
        ).order_by('order')
        view_order = list()
        view_details = dict()
        test_details = dict()

        for view in views:
            view_order.append(view.key)

            if view.type == models.SubjectViewType.TEST_VIEW:
                view_details[view.key] = {
                    'type': view.type
                }

                # Fetch tests
                test = models.SubjectTest.objects.filter(
                    subject=subject,
                    test_place=models.TestPlace.GLOBAL,
                    view__pk=view.pk
                ).only('pk', 'name', 'type', 'test_place', 'test_slug',
                       'question_mode', 'test_schedule', 'test_schedule_type', 'test_live').first()

                test_details[view.key] = {
                    'test_id': test.pk,
                    'name': test.name,
                    'test_type': test.type,
                    'test_place': test.test_place,
                    'test_slug': test.test_slug,
                    'question_mode': test.question_mode,
                    'test_schedule': test.test_schedule,
                    'test_schedule_type': test.test_schedule_type,
                    'test_live': test.test_live
                }

            else:
                if view.key == 'MI' or view.key == 'CO':
                    view_details[view.key] = {
                        'name': view.name,
                        'count': models.SubjectIntroductoryContent.objects.filter(
                            view__pk=view.pk
                        ).count(),
                        'type': view.type
                    }
                else:
                    view_details[view.key] = {
                        'name': view.name,
                        'count': models.SubjectModuleView.objects.filter(
                            view__pk=view.pk
                        ).count(),
                        'type': view.type
                    }

        response['view_order'] = view_order
        response['view_details'] = view_details
        response['test_details'] = test_details
        response['has_subject_perm'] = has_subject_perm

        return Response(response, status=status.HTTP_200_OK)


class InstituteSubjectSpecificViewCourseContentView(APIView):
    """View for getting course content of a specific subject view"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def get(self, *args, **kwargs):
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug')
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        institute = models.Institute.objects.filter(
            pk=subject.subject_class.class_institute.pk
        ).only('institute_slug').first()

        if not models.InstituteSubjectPermission.objects.filter(
                to=subject,
                invitee=self.request.user
        ).exists():
            if not models.InstitutePermission.objects.filter(
                    institute=institute,
                    role=models.InstituteRole.ADMIN,
                    invitee=self.request.user,
                    active=True,
            ).exists():
                return Response({'error': _('Permission denied.')},
                                status=status.HTTP_400_BAD_REQUEST)

        view = models.SubjectViewNames.objects.filter(
            view_subject=subject,
            key=kwargs.get('view_key')
        ).only('key').first()

        if not view:
            return Response({'error': _('Module not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        response = list()
        if view.key == 'MI' or view.key == 'CO':
            contents = models.SubjectIntroductoryContent.objects.filter(
                view__pk=view.pk)

            for content in contents:
                res = dict()
                res['id'] = content.pk
                res['name'] = content.name
                res['content_type'] = content.content_type
                res['data'] = dict()

                if content.content_type == models.SubjectIntroductionContentType.LINK:
                    res['data']['link'] = content.link
                else:
                    res['data']['file'] = self.request.build_absolute_uri('/').strip('/') + MEDIA_URL + '/' + str(content.file)
                    res['data']['can_download'] = content.can_download

                response.append(res)
        else:
            module_views = models.SubjectModuleView.objects.filter(view=view)

            for m in module_views:
                res = dict()
                res['module_view_id'] = m.pk
                res['type'] = m.type

                if m.type == models.SubjectModuleViewType.LECTURE_VIEW:
                    res['lecture_id'] = m.lecture.pk
                    res['name'] = m.lecture.name

                    if m.lecture.target_date:
                        res['target_date'] = m.lecture.target_date
                elif m.type == models.SubjectModuleViewType.TEST_VIEW:
                    res['test_id'] = m.test.pk
                    res['test_slug'] = m.test.test_slug
                    res['name'] = m.test.name
                    res['question_mode'] = m.test.question_mode
                    res['test_schedule'] = m.test.test_schedule
                    res['test_place'] = m.test.test_place
                    res['test_type'] = m.test.type
                    res['test_schedule_type'] = m.test.test_schedule_type

                response.append(res)

        return Response(response, status=status.HTTP_200_OK)


class InstituteSubjectLectureContents(APIView):
    """View for getting subject lecture course contents"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def get(self, *args, **kwargs):
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug').lower()
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        institute = models.Institute.objects.filter(
            pk=subject.subject_class.class_institute.pk
        ).only('institute_slug').first()

        if not models.InstituteSubjectPermission.objects.filter(
                to=subject,
                invitee=self.request.user
        ).exists():
            if not models.InstitutePermission.objects.filter(
                    institute=institute,
                    role=models.InstituteRole.ADMIN,
                    invitee=self.request.user,
                    active=True,
            ).exists():
                return Response({'error': _('Permission denied.')},
                                status=status.HTTP_400_BAD_REQUEST)

        lecture = models.SubjectLecture.objects.filter(
            id=kwargs.get('lecture_id')
        ).first()

        response = dict()
        response['id'] = lecture.pk
        response['name'] = lecture.name
        # response['view_name'] = lecture.view.name
        response['objectives'] = list()
        response['use_case_text'] = list()
        response['use_case_link'] = list()
        response['additional_reading_link'] = list()
        response['materials'] = list()
        response['assignments'] = list()
        response['tests'] = list()

        for objective in models.SubjectLectureUseCaseObjectives.objects.filter(
            lecture__pk=lecture.pk,
            type=models.SubjectLectureUseCaseOrObjectives.OBJECTIVES
        ):
            response['objectives'].append({
                'id': objective.pk,
                'text': objective.text
            })

        for objective in models.SubjectLectureUseCaseObjectives.objects.filter(
            lecture__pk=lecture.pk,
            type=models.SubjectLectureUseCaseOrObjectives.USE_CASE
        ):
            response['use_case_text'].append({
                'id': objective.pk,
                'text': objective.text
            })

        for use_case_link in models.SubjectAdditionalReadingUseCaseLink.objects.filter(
            lecture__pk=lecture.pk,
            type=models.SubjectAdditionalReadingOrUseCaseLinkType.USE_CASES_LINK
        ):
            response['use_case_link'].append({
                'id': use_case_link.pk,
                'name': use_case_link.name,
                'link': use_case_link.link
            })

        for additional_reading_link in models.SubjectAdditionalReadingUseCaseLink.objects.filter(
            lecture__pk=lecture.pk,
            type=models.SubjectAdditionalReadingOrUseCaseLinkType.ADDITIONAL_READING_LINK
        ):
            response['additional_reading_link'].append({
                'id': additional_reading_link.pk,
                'name': additional_reading_link.name,
                'link': additional_reading_link.link
            })

        for test in models.SubjectTest.objects.filter(
            lecture__pk=lecture.pk
        ):
            response['tests'].append({
                'test_id': test.pk,
                'test_slug': test.test_slug,
                'name': test.name,
                'question_mode': test.question_mode,
                'test_schedule': test.test_schedule,
                'test_schedule_type': test.test_schedule_type,
                'test_place': test.test_place,
                'test_type': test.type,
                'test_live': test.test_live
            })

        for lecture_materials in models.SubjectLectureMaterials.objects.filter(
            lecture__pk=lecture.pk
        ):
            res = dict()
            res['id'] = lecture_materials.pk
            res['name'] = lecture_materials.name
            res['content_type'] = lecture_materials.content_type

            if lecture_materials.content_type == models.SubjectLectureMaterialsContentType.YOUTUBE_LINK or\
                    lecture_materials.content_type == models.SubjectLectureMaterialsContentType.EXTERNAL_LINK:
                res['data'] = {
                    'link': models.SubjectLectureLinkMaterial.objects.filter(
                                lecture_material__pk=lecture_materials.pk
                            ).first().link
                }
            elif lecture_materials.content_type == models.SubjectLectureMaterialsContentType.IMAGE:
                query_data = models.SubjectLectureImageMaterial.objects.filter(
                    lecture_material__pk=lecture_materials.pk
                ).first()
                res['data'] = {
                    'file': self.request.build_absolute_uri('/').strip('/') + MEDIA_URL + str(query_data.file),
                    'can_download': query_data.can_download
                }
            elif lecture_materials.content_type == models.SubjectLectureMaterialsContentType.PDF:
                query_data = models.SubjectLecturePdfMaterial.objects.filter(
                    lecture_material__pk=lecture_materials.pk
                ).first()
                res['data'] = {
                    'file': self.request.build_absolute_uri('/').strip('/') + MEDIA_URL + str(query_data.file),
                    'can_download': query_data.can_download
                }
            elif lecture_materials.content_type == models.SubjectLectureMaterialsContentType.LIVE_CLASS:
                pass

            response['materials'].append(res)

        return Response(response, status=status.HTTP_200_OK)


class InstituteSubjectAddLectureView(APIView):
    """View for adding subject lecture by subject in-charge"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def post(self, request, *args, **kwargs):
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug')
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectPermission.objects.filter(
            to=subject,
            invitee=self.request.user
        ).exists():
            return Response({'error': _('Permission denied.')},
                            status=status.HTTP_400_BAD_REQUEST)

        view = models.SubjectViewNames.objects.filter(
            view_subject=subject,
            key=request.data.get('view_key')
        ).only('key').first()

        if not view:
            return Response({'error': _('Module not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        lecture = None
        try:
            lecture = models.SubjectLecture.objects.create(
                name=request.data.get('name'),
                target_date=request.data.get('target_date')
            )
            module_view = models.SubjectModuleView.objects.create(
                view=view,
                type=models.SubjectModuleViewType.LECTURE_VIEW,
                lecture=lecture
            )
        except ValueError as e:
            if lecture:
                lecture.delete()
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        response = {
            'module_view_id': module_view.pk,
            'lecture_id': lecture.pk,
            'name': lecture.name,
            'type': models.SubjectModuleViewType.LECTURE_VIEW
        }

        if lecture.target_date:
            response['target_date'] = str(lecture.target_date)
        else:
            response['target_date'] = ''

        return Response(response, status=status.HTTP_201_CREATED)


class InstituteSubjectEditLectureView(APIView):
    """View for editing subject lecture by subject in-charge"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def patch(self, request, *args, **kwargs):
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug')
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectPermission.objects.filter(
            to=subject,
            invitee=self.request.user
        ).exists():
            return Response({'error': _('Permission denied.')},
                            status=status.HTTP_400_BAD_REQUEST)

        lecture = models.SubjectLecture.objects.filter(
            pk=kwargs.get('lecture_id')
        ).first()

        try:
            lecture.name = request.data.get('name')
            lecture.target_date = request.data.get('target_date')
            lecture.save()
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        response = {
            'lecture_id': lecture.pk,
            'name': lecture.name
        }
        if lecture.target_date:
            response['target_date'] = str(lecture.target_date)
        else:
            response['target_date'] = ''

        return Response(response, status=status.HTTP_200_OK)


class InstituteSubjectDeleteLectureView(APIView):
    """View for deleting subject lecture by subject in-charge"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def delete(self, *args, **kwargs):
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug')
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectPermission.objects.filter(
            to=subject,
            invitee=self.request.user
        ).exists():
            return Response({'error': _('Permission denied.')},
                            status=status.HTTP_400_BAD_REQUEST)

        lecture = models.SubjectLecture.objects.filter(
            pk=kwargs.get('lecture_id')
        ).only('pk').first()

        size = 0.0

        lecture_materials = models.SubjectLectureMaterials.objects.filter(
            lecture=lecture
        ).filter(
            Q(content_type=models.SubjectLectureMaterialsContentType.PDF) | Q(
                content_type=models.SubjectLectureMaterialsContentType.IMAGE)
        ).only('content_type')

        for lm in lecture_materials:
            if lm.content_type == models.SubjectLectureMaterialsContentType.PDF:
                size += models.SubjectLecturePdfMaterial.objects.filter(
                    lecture_material__pk=lm.pk
                ).only('file').first().file.size
            elif lm.content_type == models.SubjectLectureMaterialContentType.IMAGE:
                size += models.SubjectLectureImageMaterial.objects.filter(
                    lecture_material__pk=lm.pk
                ).only('file').first().file.size

        try:
            lecture.delete()
        except Exception:
            return Response({'error': _('Internal server error.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if size:
            size = size / 1000000000  # Converting in to GB
            models.InstituteSubjectStatistics.objects.filter(
                statistics_subject=subject
            ).update(storage=F('storage') - Decimal(size))
            models.InstituteStatistics.objects.filter(
                institute__pk=subject.subject_class.class_institute.pk
            ).update(storage=F('storage') - Decimal(size))

        return Response(status=status.HTTP_204_NO_CONTENT)


class InstituteSubjectAddIntroductoryContentView(APIView):
    """Adds MI and CO introductory contents"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)
    parser_classes = (JSONParser, MultiPartParser)

    def post(self, request, *args, **kwargs):
        """Subject incharge only"""
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug')
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectPermission.objects.filter(
            to=subject,
            invitee=self.request.user
        ).exists():
            return Response({'error': _('Permission denied [Subject in-charge only].')},
                            status=status.HTTP_400_BAD_REQUEST)

        view = models.SubjectViewNames.objects.filter(
            view_subject=subject,
            key=request.data.get('view_key')
        ).only('key').first()

        if not view:
            return Response({'error': _('Module not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        institute = models.Institute.objects.filter(
            pk=subject.subject_class.class_institute.pk
        ).only('institute_slug').first()

        if not get_active_common_license(institute):
            return Response({'error': _('LMS CMS license not found or expired.')},
                            status=status.HTTP_400_BAD_REQUEST)

        storage_lic = models.InstituteLicenseStat.objects.filter(
            institute=institute
        ).only('total_storage', 'storage_license_end_date').first()

        if not storage_lic.total_storage:
            return Response({'error': _('Storage license not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if int(time.time()) * 1000 > storage_lic.storage_license_end_date:
            return Response({'error': _('Storage license expired. Please purchase storage license to continue.')},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            response = dict()
            response['view'] = view.key
            response['content_type'] = request.data.get('content_type')

            if request.data.get('content_type') == models.SubjectIntroductionContentType.IMAGE:
                res = validate_image_file(request.data.get('file'))

                if res:
                    return Response(res, status=status.HTTP_400_BAD_REQUEST)

            elif request.data.get('content_type') == models.SubjectIntroductionContentType.PDF:
                res = validate_pdf_file(request.data.get('file'), str(request.FILES['file']))

                if res:
                    return Response(res, status=status.HTTP_400_BAD_REQUEST)

            elif request.data.get('content_type') == models.SubjectIntroductionContentType.LINK:

                if not request.data.get('link'):
                    return Response({'error': _('Link is required.')},
                                    status=status.HTTP_400_BAD_REQUEST)

                obj = models.SubjectIntroductoryContent.objects.create(
                    view=view,
                    name=request.data.get('name'),
                    link=request.data.get('link'),
                    content_type=request.data.get('content_type')
                )
                response.update({
                    'id': obj.pk,
                    'name': obj.name,
                    'content_type': obj.content_type,
                    'data': {
                        'link': obj.link
                    }
                })

            if request.data.get('content_type') != models.SubjectIntroductionContentType.LINK:
                institute_stats = models.InstituteStatistics.objects.filter(
                    institute=institute
                ).only('storage').first()

                if float(storage_lic.total_storage) <= float(institute_stats.storage) + \
                        request.data.get('file').size / 1000000000:
                    return Response({'error': _('Storage limit exceeded. Please purchase additional storage to '
                                                'continue.')},
                                    status=status.HTTP_400_BAD_REQUEST)

                ser = serializer.SubjectIntroductoryFileMaterialSerializer(
                    data={
                        'file': request.data.get('file'),
                        'can_download': request.data.get('can_download'),
                        'name': request.data.get('name'),
                        'view': view.pk,
                        'content_type': request.data.get('content_type')
                    }, context={"request": request})

                if ser.is_valid():
                    ser.save()

                    models.InstituteSubjectStatistics.objects.filter(
                        statistics_subject=subject
                    ).update(storage=F('storage') + Decimal(request.data.get('file').size / 1000000000))
                    institute_stats.storage = Decimal(
                        float(institute_stats.storage) + request.data.get('file').size / 1000000000)
                    institute_stats.save()

                    response['data'] = get_file_lecture_material_data(
                        ser.data,
                        'SER',
                        ''
                    )
                    response['name'] = ser.data['name']

            return Response(response, status=status.HTTP_201_CREATED)

        except Exception as e:
            print(e)
            return Response({'error': _('Internal server error. Please report us.')},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class InstituteSubjectEditIntroductoryContentView(APIView):
    """Edits MI and CO introductory contents"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def patch(self, request, *args, **kwargs):
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug')
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectPermission.objects.filter(
            to=subject,
            invitee=self.request.user
        ).exists():
            return Response({'error': _('Permission denied.')},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            response = dict()
            obj = models.SubjectIntroductoryContent.objects.filter(
                pk=kwargs.get('content_id')
            ).first()
            obj.name = request.data.get('name')
            obj.can_download = request.data.get('can_download')

            if obj.content_type == models.SubjectIntroductionContentType.LINK:
                if not request.data.get('link'):
                    return Response({'error': _('Link is required.')},
                                    status=status.HTTP_400_BAD_REQUEST)

                obj.link = request.data.get('link')
                response.update({
                    'data': {
                        'link': obj.link
                    }
                })
            else:
                response['data'] = {
                    'file': self.request.build_absolute_uri('/').strip('/') + MEDIA_URL + str(obj.file),
                    'can_download': obj.can_download
                }

            obj.save()
            response['content_type'] = obj.content_type
            response['id'] = obj.id
            response['name'] = obj.name
            return Response(response, status=status.HTTP_201_CREATED)
        except Exception:
            return Response({'error': _('Internal server error. Please report us.')},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class InstituteSubjectDeleteIntroductoryContentView(APIView):
    """Deletes MI and CO introductory contents"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def delete(self, *args, **kwargs):
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug')
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectPermission.objects.filter(
            to=subject,
            invitee=self.request.user
        ).exists():
            return Response({'error': _('Permission denied.')},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            size = 0.0
            obj = models.SubjectIntroductoryContent.objects.filter(
                pk=kwargs.get('content_id')
            ).first()

            if not obj:
                return Response(status=status.HTTP_204_NO_CONTENT)

            if obj.content_type != models.SubjectIntroductionContentType.LINK:
                size = obj.file.size

            obj.delete()
            if size:
                size = size / 1000000000  # Converting in to GB
                models.InstituteSubjectStatistics.objects.filter(
                    statistics_subject=subject
                ).update(storage=F('storage') - Decimal(size))
                models.InstituteStatistics.objects.filter(
                    institute__pk=subject.subject_class.class_institute.pk
                ).update(storage=F('storage') - Decimal(size))

            return Response(status=status.HTTP_204_NO_CONTENT)

        except Exception:
            return Response({'error': _('Internal server error. Please report us.')},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class InstituteSubjectAddLectureMaterials(APIView):
    """Adds material to lectures"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)
    parser_classes = (JSONParser, MultiPartParser)

    def post(self, request, *args, **kwargs):
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug')
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectPermission.objects.filter(
            to=subject,
            invitee=self.request.user
        ).exists():
            return Response({'error': _('Permission denied.')},
                            status=status.HTTP_400_BAD_REQUEST)

        lecture = models.SubjectLecture.objects.filter(
            pk=kwargs.get('lecture_id')
        ).only('name').first()

        if not lecture:
            return Response({'error': _('Lecture not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        institute = models.Institute.objects.filter(
            pk=subject.subject_class.class_institute.pk
        ).only('institute_slug').first()
        license_ = get_active_common_license(institute)

        if not license_:
            return Response({'error': _('License not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        institute_stats = None
        if request.data.get('content_type') == models.SubjectLectureMaterialsContentType.IMAGE or\
                request.data.get('content_type') == models.SubjectLectureMaterialsContentType.PDF:
            institute_stats = models.InstituteStatistics.objects.filter(
                institute=institute
            ).only('storage').first()

            if float(license_.selected_license.storage) <= float(institute_stats.storage) + \
                    request.data.get('file').size / 1000000000:
                return Response({'error': _('Institute storage exceeded. Please contact us to upgrade storage.')},
                                status=status.HTTP_400_BAD_REQUEST)

        response = dict()

        try:
            subject_lecture_material = models.SubjectLectureMaterials.objects.create(
                lecture=lecture,
                name=request.data.get('name'),
                content_type=request.data.get('content_type'))

            if request.data.get('content_type') == models.SubjectLectureMaterialsContentType.IMAGE:
                validation_error = validate_image_file(request.data.get('file'))

                if validation_error:
                    subject_lecture_material.delete()
                    return Response(validation_error, status=status.HTTP_400_BAD_REQUEST)

                ser = serializer.SubjectLectureImageSerializer(
                    data={
                        "lecture_material": subject_lecture_material.pk,
                        "file": request.data.get('file'),
                        "can_download": request.data.get('can_download')
                    })

                if ser.is_valid():
                    ser.save()

                    institute_stats.storage = Decimal(float(institute_stats.storage) +
                                                      request.data.get('file').size / 1000000000)
                    institute_stats.save()
                    models.InstituteSubjectStatistics.objects.filter(
                        statistics_subject=subject
                    ).update(storage=F('storage') + Decimal(request.data.get('file').size / 1000000000))

                    response['data'] = get_file_lecture_material_data(ser.data, 'SER', '')

            elif request.data.get('content_type') == models.SubjectLectureMaterialsContentType.PDF:
                validation_error = validate_pdf_file(request.data.get('file'), str(request.FILES['file']))

                if validation_error:
                    subject_lecture_material.delete()
                    return Response(validation_error, status=status.HTTP_400_BAD_REQUEST)

                ser = serializer.SubjectLecturePdfSerializer(data={
                        "lecture_material": subject_lecture_material.pk,
                        "file": request.data.get('file'),
                        "can_download": request.data.get('can_download')
                    })

                if ser.is_valid():
                    ser.save()

                    institute_stats.storage = Decimal(float(institute_stats.storage) +
                                                      request.data.get('file').size / 1000000000)
                    institute_stats.save()
                    models.InstituteSubjectStatistics.objects.filter(
                        statistics_subject=subject
                    ).update(storage=F('storage') + Decimal(request.data.get('file').size / 1000000000))

                    response['data'] = get_file_lecture_material_data(ser.data, 'SER', '')

            elif request.data.get('content_type') == models.SubjectLectureMaterialsContentType.EXTERNAL_LINK or \
                    request.data.get('content_type') == models.SubjectLectureMaterialsContentType.YOUTUBE_LINK:
                link = models.SubjectLectureLinkMaterial.objects.create(
                    lecture_material=subject_lecture_material,
                    link=request.data.get('link')
                )
                response['data'] = {
                    'link': link.link
                }
            elif request.data.get('content_type') == models.SubjectLectureMaterialsContentType.LIVE_CLASS:
                pass

            response['id'] = subject_lecture_material.pk
            response['name'] = subject_lecture_material.name
            response['content_type'] = subject_lecture_material.content_type

            return Response(response, status=status.HTTP_201_CREATED)
        except Exception:
            models.SubjectLectureMaterials.objects.filter(
                lecture=lecture,
                name=request.data.get('name'),
                content_type=request.data.get('content_type')).delete()
            return Response({'error': _('Internal server error occurred.')},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class InstituteSubjectEditLectureMaterial(APIView):
    """Edit material of lectures"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def patch(self, request, *args, **kwargs):
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug')
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectPermission.objects.filter(
            to=subject,
            invitee=self.request.user
        ).exists():
            return Response({'error': _('Permission denied.')},
                            status=status.HTTP_400_BAD_REQUEST)

        lecture_material = models.SubjectLectureMaterials.objects.filter(
            pk=kwargs.get('lecture_material_id')
        ).first()

        if not lecture_material:
            return Response({'error': _('Lecture material not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        lecture_material.name = request.data.get('name')
        try:
            lecture_material.save()
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        response = dict()

        try:
            if request.data.get('content_type') == models.SubjectLectureMaterialsContentType.IMAGE:
                query_data = models.SubjectLectureImageMaterial.objects.filter(
                    lecture_material=lecture_material
                ).first()
                query_data.can_download = request.data.get('can_download')
                query_data.save()

                response['data'] = get_file_lecture_material_data(
                    query_data, 'OBJ', self.request.build_absolute_uri('/').strip('/') + MEDIA_URL)

            elif request.data.get('content_type') == models.SubjectLectureMaterialsContentType.PDF:
                query_data = models.SubjectLecturePdfMaterial.objects.filter(
                    lecture_material=lecture_material
                ).first()
                query_data.can_download = request.data.get('can_download')
                query_data.save()

                response['data'] = get_file_lecture_material_data(
                    query_data, 'OBJ', self.request.build_absolute_uri('/').strip('/') + MEDIA_URL)

            elif request.data.get('content_type') == models.SubjectLectureMaterialsContentType.EXTERNAL_LINK or \
                    request.data.get('content_type') == models.SubjectLectureMaterialsContentType.YOUTUBE_LINK:
                query_data = models.SubjectLectureLinkMaterial.objects.filter(
                    lecture_material=lecture_material
                ).first()
                query_data.link = request.data.get('link')
                query_data.save()

                response['data'] = {
                    'id': query_data.pk,
                    'link': query_data.link
                }
            elif request.data.get('content_type') == models.SubjectLectureMaterialsContentType.LIVE_CLASS:
                pass

            response['id'] = lecture_material.pk
            response['name'] = lecture_material.name
            response['content_type'] = lecture_material.content_type

            return Response(response, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({'error': _('Internal server error occurred.')},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class InstituteSubjectDeleteLectureMaterial(APIView):
    """Deletes material of lectures"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def delete(self, *args, **kwargs):
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug')
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectPermission.objects.filter(
            to=subject,
            invitee=self.request.user
        ).exists():
            return Response({'error': _('Permission denied.')},
                            status=status.HTTP_400_BAD_REQUEST)

        size = 0.0
        lecture_material = models.SubjectLectureMaterials.objects.filter(
            pk=kwargs.get('lecture_material_id')
        ).first()

        if not lecture_material:
            return Response(status=status.HTTP_204_NO_CONTENT)

        try:
            if lecture_material.content_type == models.SubjectLectureMaterialsContentType.IMAGE:
                size = models.SubjectLectureImageMaterial.objects.filter(
                    lecture_material=lecture_material
                ).only('file').first().file.size
            elif lecture_material.content_type == models.SubjectLectureMaterialsContentType.PDF:
                size = models.SubjectLecturePdfMaterial.objects.filter(
                    lecture_material=lecture_material
                ).only('file').first().file.size

            lecture_material.delete()

            if size:
                size = size / 1000000000  # Converting in to GB
                models.InstituteSubjectStatistics.objects.filter(
                    statistics_subject=subject
                ).update(storage=F('storage') - Decimal(size))
                models.InstituteStatistics.objects.filter(
                    institute__pk=subject.subject_class.class_institute.pk
                ).update(storage=F('storage') - Decimal(size))

            return Response(status=status.HTTP_204_NO_CONTENT)
        except Exception:
            return Response({'error': _('Internal server error occurred.')},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class InstituteAddLectureUseCaseOrAdditionalReading(APIView):
    """Adds additional reading or use case links to lectures"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def post(self, request, *args, **kwargs):
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug')
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectPermission.objects.filter(
                to=subject,
                invitee=self.request.user
        ).exists():
            return Response({'error': _('Permission denied.')},
                            status=status.HTTP_400_BAD_REQUEST)

        lecture = models.SubjectLecture.objects.filter(
            pk=kwargs.get('lecture_id')
        ).only('name').first()

        if not lecture:
            return Response({'error': _('Lecture not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            link = models.SubjectAdditionalReadingUseCaseLink.objects.create(
                lecture=lecture,
                name=request.data.get('name'),
                link=request.data.get('link'),
                type=request.data.get('type'))
            return Response({
                'id': link.id,
                'name': link.name,
                'link': link.link
            }, status=status.HTTP_201_CREATED)
        except ValueError as e:
            return Response({'error': str(e)},
                            status=status.HTTP_400_BAD_REQUEST)


class InstituteEditLectureUseCaseOrAdditionalReading(APIView):
    """Adds additional reading or use case links to lectures"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def patch(self, request, *args, **kwargs):
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug')
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectPermission.objects.filter(
                to=subject,
                invitee=self.request.user
        ).exists():
            return Response({'error': _('Permission denied.')},
                            status=status.HTTP_400_BAD_REQUEST)

        content = models.SubjectAdditionalReadingUseCaseLink.objects.filter(
            pk=kwargs.get('content_id')
        ).only('name', 'link').first()

        if not content:
            return Response({'error': _('Content not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            content.name = request.data.get('name')
            content.link = request.data.get('link')
            content.save()

            return Response({
                'id': content.id,
                'name': content.name,
                'link': content.link
            }, status=status.HTTP_200_OK)

        except ValueError as e:
            return Response({'error': str(e)},
                            status=status.HTTP_400_BAD_REQUEST)


class InstituteDeleteLectureUseCaseOrAdditionalReading(APIView):
    """Deletes additional reading or use case links of lectures"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def delete(self, *args, **kwargs):
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug')
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectPermission.objects.filter(
                to=subject,
                invitee=self.request.user
        ).exists():
            return Response({'error': _('Permission denied.')},
                            status=status.HTTP_400_BAD_REQUEST)

        models.SubjectAdditionalReadingUseCaseLink.objects.filter(
            pk=kwargs.get('content_id')
        ).delete()

        return Response(status.HTTP_204_NO_CONTENT)


class InstituteAddLectureObjectiveOrUseCaseText(APIView):
    """Adds lecture objectives or use case text to lectures"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def post(self, request, *args, **kwargs):
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug')
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectPermission.objects.filter(
                to=subject,
                invitee=self.request.user
        ).exists():
            return Response({'error': _('Permission denied.')},
                            status=status.HTTP_400_BAD_REQUEST)

        lecture = models.SubjectLecture.objects.filter(
            pk=kwargs.get('lecture_id')
        ).only('name').first()

        if not lecture:
            return Response({'error': _('Lecture not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            content = models.SubjectLectureUseCaseObjectives.objects.create(
                lecture=lecture,
                text=request.data.get('text'),
                type=request.data.get('type'))

            return Response({
                'id': content.id,
                'text': content.text
            }, status=status.HTTP_201_CREATED)

        except ValueError as e:
            return Response({'error': str(e)},
                            status=status.HTTP_400_BAD_REQUEST)


class InstituteEditLectureObjectiveOrUseCaseText(APIView):
    """Edits lecture objectives or use case text to lectures"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def patch(self, request, *args, **kwargs):
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug')
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectPermission.objects.filter(
                to=subject,
                invitee=self.request.user
        ).exists():
            return Response({'error': _('Permission denied.')},
                            status=status.HTTP_400_BAD_REQUEST)

        content = models.SubjectLectureUseCaseObjectives.objects.filter(
            pk=kwargs.get('content_id')
        ).first()

        if not content:
            return Response({'error': _('Content not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            content.text = request.data.get('text')
            content.save()

            return Response({
                'id': content.id,
                'text': content.text
            }, status=status.HTTP_200_OK)

        except ValueError as e:
            return Response({'error': str(e)},
                            status=status.HTTP_400_BAD_REQUEST)


class InstituteDeleteLectureObjectiveOrUseCaseText(APIView):
    """Deletes lecture objectives or use case text of lectures"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def delete(self, *args, **kwargs):
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug')
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectPermission.objects.filter(
                to=subject,
                invitee=self.request.user
        ).exists():
            return Response({'error': _('Permission denied.')},
                            status=status.HTTP_400_BAD_REQUEST)

        models.SubjectLectureUseCaseObjectives.objects.filter(
            pk=kwargs.get('content_id')
        ).delete()

        return Response(status.HTTP_204_NO_CONTENT)


class InstituteSubjectDeleteModuleView(APIView):
    """View for deleting subject module"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def delete(self, request, *args, **kwargs):
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug').lower()
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectPermission.objects.filter(
                to=subject,
                invitee=self.request.user
        ).exists():
            return Response({'error': _('Permission denied.')},
                            status=status.HTTP_400_BAD_REQUEST)

        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug').lower()
        ).only('institute_slug').first()

        if not institute:
            return Response({'error': _('Institute not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        view = models.SubjectViewNames.objects.filter(
            view_subject=subject,
            key=kwargs.get('view_key')
        ).first()

        if not view:
            return Response(status=status.HTTP_204_NO_CONTENT)

        if view.key == 'MI' or view.key == 'CO':
            return Response({'error': _('This module can not be deleted. You can delete its content manually.')},
                            status=status.HTTP_400_BAD_REQUEST)

        # Finding the statistics of the content to be deleted
        module_views = models.SubjectModuleView.objects.filter(view=view)
        total_size = 0

        for mv in module_views:
            if mv.type == models.SubjectModuleViewType.LECTURE_VIEW:
                study_materials = models.SubjectLectureMaterials.objects.filter(
                    lecture__pk=mv.lecture.pk
                ).filter(
                    Q(content_type=models.SubjectLectureMaterialsContentType.IMAGE) | Q(
                        content_type=models.SubjectLectureMaterialsContentType.PDF))
                for material in study_materials:
                    if material.content_type == models.StudyMaterialContentType.IMAGE:
                        total_size += float(
                            models.SubjectLectureImageMaterial.objects.filter(
                                lecture_material__pk=material.pk
                            ).first().file.size
                        )
                    elif material.content_type == models.StudyMaterialContentType.PDF:
                        total_size += float(
                            models.SubjectLecturePdfMaterial.objects.filter(
                                lecture_material__pk=material.pk
                            ).first().file.size
                        )
                # Find size of assignments
                # Find size of tests
            elif mv.type == models.SubjectModuleViewType.TEST_VIEW:
                # Find size of test materials space
                pass

        try:
            view.delete()

            # Updating statistics
            if total_size:
                total_size = Decimal(total_size / 1000000000)  # Converting into GB
                models.InstituteSubjectStatistics.objects.filter(
                    statistics_subject=subject
                ).update(storage=F('storage') - total_size)
                models.InstituteStatistics.objects.filter(
                    institute=institute
                ).update(storage=F('storage') - total_size)

            return Response(status=status.HTTP_204_NO_CONTENT)
        except Exception:
            return Response({'error': _('Internal server error.')},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class InstituteEditSubjectModuleViewName(APIView):
    """View for editing subject module name"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def patch(self, request, *args, **kwargs):
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug').lower()
        ).first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectPermission.objects.filter(
            to=subject,
            invitee=self.request.user
        ).exists():
            return Response({'error': _('Permission denied.')},
                            status=status.HTTP_400_BAD_REQUEST)

        view = models.SubjectViewNames.objects.filter(
            view_subject=subject,
            key=kwargs.get('view_key')
        ).first()

        if not view:
            return Response({'error': _('Module not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            view.name = request.data.get('name')
            view.save()

            return Response({'name': view.name},
                            status=status.HTTP_200_OK)
        except ValueError as e:
            return Response({'error': str(e)},
                            status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            return Response({'error': 'Internal server error'},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class InstituteStudentCourseListView(APIView):
    """
    View for listing all course of institute.
    """
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsStudent)

    def get(self, *args, **kwargs):
        institute_invites = models.InstituteStudents.objects.filter(
            invitee=self.request.user,
            active=True
        ).only('institute').order_by('-created_on')

        response = {
            'view_order': [],
            'courses': dict(),
            'class_names': dict(),
            'favourite_courses': dict()
        }

        if institute_invites:
            response['view_order'].append({
                'name': 'Favourite Courses',
                'institute_slug': 'BOOKMARKED'
            })
            response['favourite_courses']['BOOKMARKED'] = list()
            for invite in institute_invites:
                institute_slug = invite.institute.institute_slug
                institute_pk = invite.institute.pk
                response['view_order'].append({
                    'name': invite.institute.name,
                    'institute_slug': institute_slug
                })
                response['courses'][institute_slug] = list()
                class_invite = models.InstituteClassStudents.objects.filter(
                    institute_class__class_institute__pk=institute_pk,
                    institute_student__invitee=self.request.user,
                    active=True
                ).only('institute_class').first()

                if class_invite:
                    response['class_names'][institute_slug] = class_invite.institute_class.name
                    class_slug = class_invite.institute_class.class_slug
                    class_pk = class_invite.institute_class.pk
                    class_subjects = models.InstituteSubject.objects.filter(
                        subject_class__pk=class_pk
                    ).only('name', 'subject_slug')

                    for subject in class_subjects:
                        if models.InstituteSubjectStudents.objects.filter(
                            institute_subject__pk=subject.pk,
                            institute_student__invitee=self.request.user
                        ).exists():
                            subject_course_details = {
                                'institute_slug': institute_slug,
                                'class_slug': class_slug,
                                'subject_slug': subject.subject_slug,
                                'subject_name': subject.name,
                                'subject_description': 'Description of the subject',
                                'subject_id': subject.pk,
                                'image': ''
                            }
                            if models.SubjectBookmarked.objects.filter(
                                subject__pk=subject.pk,
                                user=self.request.user
                            ).exists():
                                subject_course_details['BOOKMARKED'] = True
                                response['favourite_courses']['BOOKMARKED'].append(subject_course_details)
                            else:
                                subject_course_details['BOOKMARKED'] = False
                                response['courses'][institute_slug].append(subject_course_details)

        return Response(response, status=status.HTTP_200_OK)


class BookmarkInstituteCourse(APIView):
    """View for bookmarking course by authenticated user"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsStudent)

    def post(self, request, *args, **kwargs):
        subject = models.InstituteSubject.objects.filter(
            pk=request.data.get('subject_id')
        ).only('subject_class').first()

        if not subject:
            return Response({'error': _('Course may have been removed.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectStudents.objects.filter(
            institute_subject__pk=subject.pk,
            institute_student__invitee=self.request.user,
            active=True
        ).exists():
            return Response({'error': _('Permission denied.')},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            if models.SubjectBookmarked.objects.filter(
                subject=subject,
                user=self.request.user
            ).exists():
                models.SubjectBookmarked.objects.filter(
                    subject=subject,
                    user=self.request.user
                ).delete()
                return Response(status=status.HTTP_204_NO_CONTENT)
            else:
                models.SubjectBookmarked.objects.create(
                    subject=subject,
                    user=self.request.user
                )
                return Response({'status': 'OK'},
                                status=status.HTTP_201_CREATED)
        except Exception:
            return Response({'error': _('Internal server error.')},
                            status=status.HTTP_400_BAD_REQUEST)


class ListSubjectPeers(APIView):
    """View for listing students and faculties of subject"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsStudent)

    def get(self, *args, **kwargs):
        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug')
        ).only('institute_slug').first()

        if not institute:
            return Response({'error': _('Institute not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug').lower()
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectStudents.objects.filter(
            institute_subject__pk=subject.pk,
            institute_student__invitee=self.request.user,
            active=True,
            is_banned=False
        ).exists():
            return Response({'error': _('Permission denied.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if models.InstituteClassStudents.objects.filter(
            institute_class__pk=institute.pk,
            institute_student__invitee=self.request.user,
            is_banned=True
        ):
            return Response({'error': _('Permission denied.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not get_active_common_license(institute):
            return Response({'error': _('Active license not found or expired. Contact your institute.')},
                            status=status.HTTP_400_BAD_REQUEST)

        students = list()
        instructors = list()

        for invite in models.InstituteSubjectStudents.objects.filter(
            institute_subject__pk=subject.pk,
            active=True,
            is_banned=False
        ).only('institute_student').order_by('institute_student__enrollment_no'):
            student_details = dict()
            student_details['image'] = ''
            student_details['user_id'] = invite.institute_student.invitee.pk
            student_details['name'] = invite.institute_student.first_name + ' ' + invite.institute_student.last_name
            student_details['enrollment_no'] = invite.institute_student.enrollment_no
            students.append(student_details)

        for invite in models.InstituteSubjectPermission.objects.filter(
            to__pk=subject.pk
        ).order_by('created_on'):
            instructor_details = dict()
            instructor_details['image'] = ''
            instructor_details['user_id'] = invite.invitee.pk

            if invite.invitee.user_profile.first_name:
                instructor_details['name'] = invite.invitee.user_profile.first_name + ' ' +\
                                             invite.invitee.user_profile.last_name
            else:
                instructor_details['name'] = str(invite.invitee)

            instructors.append(instructor_details)

        return Response({
            'instructors': instructors,
            'students': students,
            'view_order': ['Instructors', 'Students']
        }, status=status.HTTP_200_OK)


class InstituteSubjectCoursePreviewMinDetails(APIView):
    """
    View for getting min course statistics by admin, subject incharge and
    permitted student.
    """
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacherOrStudent)

    def get(self, *args, **kwargs):
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug').lower()
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug').lower()
        ).only('institute_slug').first()

        if not institute:
            return Response({'error': _('Institute not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        requester_type = None
        if self.request.user.is_student:
            if not models.InstituteSubjectStudents.objects.filter(
                institute_subject__pk=subject.pk,
                institute_student__invitee=self.request.user
            ).exists():
                return Response({'error': _('Permission denied.')},
                                status=status.HTTP_400_BAD_REQUEST)

        elif self.request.user.is_teacher:
            if not models.InstituteSubjectPermission.objects.filter(
                    to=subject,
                    invitee=self.request.user
            ).exists():
                if not models.InstitutePermission.objects.filter(
                    institute=institute,
                    invitee=self.request.user,
                    role=models.InstituteRole.ADMIN,
                    active=True
                ).exists():
                    return Response({'error': _('Permission denied.')},
                                    status=status.HTTP_400_BAD_REQUEST)

        order = get_active_common_license(institute)

        if not order:
            return Response({'error': _('Institute license expired or not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        response = dict()
        response['instructors'] = list()
        view_order = list()
        view_details = dict()
        instructors = models.InstituteSubjectPermission.objects.filter(
            to=subject,
            invitee=self.request.user
        )
        for instructor in instructors:
            instructor_details = dict()
            user_profile = models.UserProfile.objects.filter(
                user__pk=instructor.invitee.pk
            ).first()
            instructor_details['id'] = instructor.invitee.pk
            instructor_details['name'] = user_profile.first_name + ' ' + user_profile.last_name
            instructor_details['email'] = instructor.invitee.email
            instructor_details['image'] = None
            response['instructors'].append(instructor_details)

        views = models.SubjectViewNames.objects.filter(
            view_subject=subject
        ).order_by('order')

        for view in views:
            view_order.append(view.key)
            subject_view_model = models.SubjectViewNames.objects.filter(
                view_subject=subject,
                key=view.key
            ).first()
            view_details[view.key] = {
                'name': view.name,
                'count': models.InstituteSubjectCourseContent.objects.filter(
                    course_content_subject=subject,
                    view=subject_view_model
                ).count()
            }
            if view.key != 'MI' and view.key != 'CO':
                weeks = models.SubjectViewWeek.objects.filter(
                    week_view=view
                ).order_by('value')
                week_value_list = list()
                for week in weeks:
                    view_details[view.key][week.value] = models.InstituteSubjectCourseContent.objects.filter(
                        course_content_subject=subject,
                        view=subject_view_model,
                        week=week
                    ).count()
                    week_value_list.append(week.value)
                view_details[view.key]['weeks'] = week_value_list

        response['view_order'] = view_order
        response['view_details'] = view_details

        return Response(response, status=status.HTTP_200_OK)


class PreviewInstituteSubjectSpecificViewContents(APIView):
    """View for getting course content of a specific subject by teacher or permitted student"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacherOrStudent)

    def _get_data(self, content_type, data_id):
        if content_type == models.StudyMaterialContentType.EXTERNAL_LINK:
            query_data = models.SubjectExternalLinkStudyMaterial.objects.filter(
                external_link_study_material__pk=data_id
            ).first()
            return get_external_link_study_material_data(
                query_data,
                'OBJ'
            )
        elif content_type == models.StudyMaterialContentType.IMAGE:
            query_data = models.SubjectImageStudyMaterial.objects.filter(
                image_study_material__pk=data_id
            ).first()
            return get_image_study_material_data(
                query_data,
                'OBJ',
                self.request.build_absolute_uri('/').strip("/") + MEDIA_URL
            )
        elif content_type == models.StudyMaterialContentType.VIDEO:
            query_data = models.SubjectVideoStudyMaterial.objects.filter(
                video_study_material__pk=data_id
            ).first()
            return get_video_study_material_data(
                query_data,
                'OBJ',
                self.request.build_absolute_uri('/').strip("/") + MEDIA_URL
            )
        elif content_type == models.StudyMaterialContentType.PDF:
            query_data = models.SubjectPdfStudyMaterial.objects.filter(
                pdf_study_material__pk=data_id
            ).first()
            return get_pdf_study_material_data(
                query_data,
                'OBJ',
                self.request.build_absolute_uri('/').strip("/") + MEDIA_URL
            )

    def get(self, *args, **kwargs):
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug').lower()
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': 'Subject not found.'},
                            status=status.HTTP_400_BAD_REQUEST)

        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug').lower()
        ).first()

        if not institute:
            return Response({'error': _('Institute not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if self.request.user.is_student:
            if not models.InstituteSubjectStudents.objects.filter(
                    institute_subject__pk=subject.pk,
                    institute_student__invitee=self.request.user
            ).exists():
                return Response({'error': _('Permission denied.')},
                                status=status.HTTP_400_BAD_REQUEST)

            if not get_active_common_license(institute):
                return Response({'error': _('Institute has no active license. Contact institute.')},
                                status=status.HTTP_400_BAD_REQUEST)

        elif self.request.user.is_teacher:
            if not models.InstituteSubjectPermission.objects.filter(
                    to=subject,
                    invitee=self.request.user
            ).exists():
                if not models.InstitutePermission.objects.filter(
                        institute=institute,
                        invitee=self.request.user,
                        role=models.InstituteRole.ADMIN,
                        active=True
                ).exists():
                    return Response({'error': _('Permission denied.')},
                                    status=status.HTTP_400_BAD_REQUEST)

            if not get_active_common_license(institute):
                return Response({'error': _('Institute license expired or not found.')},
                                status=status.HTTP_400_BAD_REQUEST)

        view = models.SubjectViewNames.objects.filter(
            view_subject=subject,
            key=kwargs.get('view_key')
        ).only('view_subject', 'key').first()

        if not view:
            return Response({'error': _('Module not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        data = models.InstituteSubjectCourseContent.objects.filter(
            course_content_subject__pk=subject.pk,
            view__pk=view.pk).order_by('order')
        response = None

        if view.key != 'MI' and view.key != 'CO':
            response = dict()
            view_weeks = models.SubjectViewWeek.objects.filter(
                week_view=view).only('value')
            for week in view_weeks:
                week_data = data.filter(week__value=week.value)
                week_data_response = list()
                for d in week_data:
                    res = get_study_material_content_details(d, 'OBJ')
                    res['week'] = week.value
                    res['data'] = self._get_data(
                        d.content_type,
                        d.pk
                    )
                    week_data_response.append(res)
                response[week.value] = week_data_response
        else:
            response = list()
            for d in data:
                res = get_study_material_content_details(d, 'OBJ')
                res['data'] = self._get_data(
                    d.content_type,
                    d.pk
                )
                response.append(res)

        return Response(response, status=status.HTTP_200_OK)


class InstituteSubjectCourseContentAskQuestionView(APIView):
    """View for asking question"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsStudent)

    def post(self, request, *args, **kwargs):
        """Only permitted student can ask question."""
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug').lower()
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug').lower()
        ).only('institute_slug').first()

        if not institute:
            return Response({'error': _('Institute not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectStudents.objects.filter(
                institute_subject=subject,
                institute_student__invitee=self.request.user,
                active=True
        ).exists():
                return Response({'error': _('Permission denied.')},
                                status=status.HTTP_400_BAD_REQUEST)

        course_content = models.InstituteSubjectCourseContent.objects.filter(
            pk=kwargs.get('course_content_id')
        ).only('order').first()

        if not course_content:
            return Response({'error': _('Course content not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not get_active_common_license(institute):
            return Response({'error': _('Active license not found or expired.')},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            question = models.InstituteSubjectCourseContentQuestions.objects.create(
                course_content=course_content,
                user=self.request.user,
                anonymous=request.data.get('anonymous'),
                question=request.data.get('question'),
                description=request.data.get('description'),
                rgb_color=request.data.get('rgb_color')
            )
            response = {
                'id': question.pk,
                'question': question.question,
                'description': question.description,
                'rgb_color': question.rgb_color,
                'anonymous': question.anonymous,
                'created_on': question.created_on,
                'upvotes': 0,
                'answer_count': 0,
                'user_id': self.request.user.pk,
                'upvoted': False,
                'edited': question.edited
            }
            if question.anonymous:
                response['user'] = 'Anonymous User(self)'
            else:
                user_info = models.InstituteStudents.objects.filter(
                    invitee=self.request.user,
                    institute=institute
                ).only('first_name', 'last_name').first()
                if user_info.first_name and user_info.last_name:
                    response['user'] = user_info.first_name + ' ' + user_info.last_name
                else:
                    response['user'] = str(self.request.user)
            return Response(response, status=status.HTTP_201_CREATED)
        except ValueError as e:
            return Response({'error': str(e)},
                            status=status.HTTP_400_BAD_REQUEST)
        except IntegrityError:
            return Response({'error': _('This question already exists.')},
                            status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            print(e)
            return Response({'error': _('Internal server error. Contact us.')},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class InstituteSubjectCourseContentAnswerQuestionView(APIView):
    """View for answering question"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacherOrStudent)

    def post(self, request, *args, **kwargs):
        """Only permitted subject incharge and permitted student can ask question."""
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug').lower()
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug').lower()
        ).only('institute_slug').first()

        if not institute:
            return Response({'error': _('Institute not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        role = None

        if not models.InstituteSubjectStudents.objects.filter(
                institute_subject=subject,
                institute_student__invitee=self.request.user,
                active=True
        ).exists():
            if not models.InstituteSubjectPermission.objects.filter(
                to=subject,
                invitee=self.request.user
            ).exists():
                return Response({'error': _('Permission denied.')},
                                status=status.HTTP_400_BAD_REQUEST)
            else:
                role = 'Instructor'
        else:
            role = 'Student'

        question = models.InstituteSubjectCourseContentQuestions.objects.filter(
            pk=kwargs.get('question_pk')
        ).only('anonymous').first()

        if not question:
            return Response({'error': _('Question may have been deleted or not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not get_active_common_license(institute):
            return Response({'error': _('Active license not found or expired.')},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            ans = models.InstituteSubjectCourseContentAnswer.objects.create(
                content_question=question,
                answer=request.data.get('answer'),
                user=self.request.user,
                rgb_color=request.data.get('rgb_color'),
                anonymous=request.data.get('anonymous')
            )
            response = {
                'id': ans.pk,
                'answer': ans.answer,
                'rgb_color': ans.rgb_color,
                'anonymous': ans.anonymous,
                'role': role,
                'pin': ans.pin,
                'created_on': ans.created_on,
                'content_question_id': ans.content_question.pk,
                'upvotes': 0,
                'user_id': self.request.user.pk,
                'upvoted': False,
                'edited': ans.edited
            }

            if ans.anonymous:
                response['user'] = 'Anonymous User(self)'
            else:
                user_details = None

                if role == 'Student':
                    user_details = models.InstituteStudents.objects.filter(
                        invitee=self.request.user,
                        institute=institute
                    ).only('first_name', 'last_name').first()
                else:
                    user_details = models.UserProfile.objects.filter(
                        user=self.request.user
                    ).only('first_name', 'last_name').first()

                if user_details.first_name and user_details.last_name:
                    response['user'] = user_details.first_name + ' ' + user_details.last_name
                else:
                    response['user'] = str(self.request.user)

            return Response(response, status=status.HTTP_201_CREATED)
        except ValueError as e:
            return Response({'error': str(e)},
                            status=status.HTTP_400_BAD_REQUEST)
        except IntegrityError:
            return Response({'error': _('This answer has already been posted.')},
                            status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            return Response({'error': _('Internal server error occurred.')},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class InstituteSubjectUpvoteDownvoteQuestionView(APIView):
    """View for upvoting and downvoting question by permitted instructor and student"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacherOrStudent)

    def post(self, request, *args, **kwargs):
        print(request.data)
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug').lower()
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug').lower()
        ).only('institute_slug').first()

        if not institute:
            return Response({'error': _('Institute not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectStudents.objects.filter(
            institute_subject=subject,
            institute_student__invitee=self.request.user,
            active=True,
            is_banned=False
        ).exists():
            if not models.InstituteSubjectPermission.objects.filter(
                    to=subject,
                    invitee=self.request.user
            ).exists():
                return Response({'error': _('Permission denied.')},
                                status=status.HTTP_400_BAD_REQUEST)

        question = models.InstituteSubjectCourseContentQuestions.objects.filter(
            pk=kwargs.get('question_pk')
        ).only('user').first()

        if not question:
            return Response({'error': _('Question may have been deleted or not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if question.user.pk == self.request.user.pk:
            return Response({'error': _('Bad Request.')},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            if 'upvote' in request.data:
                res = models.InstituteSubjectCourseContentQuestionUpvote.objects.create(
                    course_content_question=question,
                    user=self.request.user
                )
                return Response({'upvoted': True, 'question_id': res.course_content_question.pk},
                                status=status.HTTP_201_CREATED)
            else:
                res = models.InstituteSubjectCourseContentQuestionUpvote.objects.filter(
                    course_content_question=question,
                    user=self.request.user
                ).first()

                if not res or res.user.pk != self.request.user.pk:
                    return Response({'error': _('Question was not upvoted.')},
                                    status=status.HTTP_400_BAD_REQUEST)
                else:
                    res.delete()

                return Response(status=status.HTTP_204_NO_CONTENT)
        except IntegrityError:
            return Response({'error': _('Question already upvoted.')},
                            status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            return Response({'error': _('Internal server error occured.')},
                             status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class InstituteSubjectUpvoteDownvoteAnswerView(APIView):
    """View for upvoting and downvoting answer by permitted instructor and student"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacherOrStudent)

    def post(self, request, *args, **kwargs):
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug').lower()
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug').lower()
        ).first()

        if not institute:
            return Response({'error': _('Institute not found.')},
                            status=status.HTTP_400_BAD_REQUEST)


        if not models.InstituteSubjectStudents.objects.filter(
                institute_subject=subject,
                institute_student__invitee=self.request.user,
                active=True
        ).exists():
            if not models.InstituteSubjectPermission.objects.filter(
                    to=subject,
                    invitee=self.request.user
            ).exists():
                return Response({'error': _('Permission denied.')},
                                status=status.HTTP_400_BAD_REQUEST)

        answer = models.InstituteSubjectCourseContentAnswer.objects.filter(
            pk=kwargs.get('answer_pk')
        ).only('user').first()

        if not answer:
            return Response({'error': _('Answer may have been deleted or not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if answer.user.pk == self.request.user.pk:
            return Response({'error': _('Bad Request.')},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            if 'upvote' in request.data:
                res = models.InstituteSubjectCourseContentAnswerUpvote.objects.create(
                    course_content_answer=answer,
                    user=self.request.user
                )
                return Response({'upvoted': True, 'answer_id': res.course_content_answer.pk},
                                status=status.HTTP_201_CREATED)
            else:
                res = models.InstituteSubjectCourseContentAnswerUpvote.objects.filter(
                    course_content_answer=answer,
                    user=self.request.user
                ).first()

                if not res or res.user.pk != self.request.user.pk:
                    return Response({'error': 'Permission denied.'},
                                    status=status.HTTP_400_BAD_REQUEST)
                else:
                    res.delete()

                return Response(status=status.HTTP_204_NO_CONTENT)
        except IntegrityError:
            return Response({'error': _('Answer already upvoted.')},
                            status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            return Response({'error': _('Internal server error occured.')},
                             status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class InstituteSubjectCourseContentDeleteQuestionView(APIView):
    """View for deleting question by self"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacherOrStudent)

    def delete(self, *args, **kwargs):
        question = models.InstituteSubjectCourseContentQuestions.objects.filter(
            pk=kwargs.get('question_pk')
        ).first()

        if not question:
            return Response(status=status.HTTP_204_NO_CONTENT)

        if question.user.pk != self.request.user.pk:
            return Response({'error': _('Permission denied.')},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            question.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        except Exception:
            return Response({'error': _('Internal server error.')},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class InstituteSubjectCourseContentDeleteAnswerView(APIView):
    """View for deleting answer by self"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacherOrStudent)

    def delete(self, *args, **kwargs):
        answer = models.InstituteSubjectCourseContentAnswer.objects.filter(
            pk=kwargs.get('answer_pk')
        ).first()

        if not answer:
            return Response(status=status.HTTP_204_NO_CONTENT)

        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug').lower()
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if answer.user.pk != self.request.user.pk:
            return Response({'error': _('Permission denied.')},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            answer.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        except Exception:
            return Response({'error': _('Internal server error.')},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class InstituteSubjectCourseContentEditAnswerView(APIView):
    """View for editing answer by self"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacherOrStudent)

    def patch(self, request, *args, **kwargs):
        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug')
        ).only('institute_slug').first()

        if not institute:
            return Response({'error': _('Institute not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        ans = models.InstituteSubjectCourseContentAnswer.objects.filter(
            pk=kwargs.get('answer_pk')
        ).first()

        if not ans:
            return Response({'error': _('Answer may have been deleted.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if ans.user.pk != self.request.user.pk:
            return Response({'error': _('Permission denied.')},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            if 'anonymous' in request.data:
                ans.anonymous = request.data.get('anonymous')
            if 'answer' in request.data:
                ans.answer = request.data.get('answer')
            ans.edited = True
            ans.save()

            response = {
                'id': ans.pk,
                'created_on': ans.created_on,
                'pin': ans.pin,
                'anonymous': ans.anonymous,
                'rgb_color': ans.rgb_color,
                'answer': ans.answer,
                'edited': ans.edited
            }

            if ans.anonymous:
                if ans.user and ans.user.pk == self.request.user.pk:
                    response['user_id'] = ans.user.pk
                    response['user'] = 'Anonymous User(self)'
                else:
                    response['user'] = 'Anonymous User'

            elif ans.user:
                response['user_id'] = ans.user.pk
                is_student = models.InstituteStudents.objects.filter(
                    invitee__pk=ans.user.pk
                ).exists()
                user_data = None

                if is_student:
                    user_data = models.InstituteStudents.objects.filter(
                        invitee__pk=ans.user.pk,
                        institute=institute
                    ).only('first_name', 'last_name').first()
                else:
                    user_data = models.UserProfile.objects.filter(
                        user__pk=ans.user.pk
                    ).only('first_name', 'last_name').first()

                if user_data and user_data.first_name and user_data.last_name:
                    response['user'] = user_data.first_name + ' ' + user_data.last_name
                else:
                    response['user'] = str(ans.user)
            else:
                response['user'] = 'Deleted User'

            response['upvotes'] = models.InstituteSubjectCourseContentAnswerUpvote.objects.filter(
                course_content_answer__pk=ans.pk
            ).count()
            response['upvoted'] = models.InstituteSubjectCourseContentAnswerUpvote.objects.filter(
                course_content_answer__pk=ans.pk,
                user=self.request.user
            ).exists()

            return Response(response, status=status.HTTP_200_OK)
        except Exception:
            return Response({'error': _('Internal server error.')},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class InstituteSubjectCourseContentEditQuestionView(APIView):
    """View for editing question by question asker"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsStudent)

    def patch(self, request, *args, **kwargs):
        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug')
        ).only('institute_slug').first()

        if not institute:
            return Response({'error': _('Institute not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        question = models.InstituteSubjectCourseContentQuestions.objects.filter(
            pk=kwargs.get('question_pk')
        ).first()

        if not question:
            return Response({'error': _('Question may have been deleted.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if question.user.pk != self.request.user.pk:
            return Response({'error': _('Permission denied.')},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            if 'anonymous' in request.data:
                question.anonymous = request.data.get('anonymous')
            if 'question' in request.data:
                question.question = request.data.get('question')
            if 'description' in request.data:
                question.description = request.data.get('description')
            question.edited = True
            question.save()

            response = dict()
            response['id'] = question.id
            response['question'] = question.question
            response['rgb_color'] = question.rgb_color
            response['created_on'] = question.created_on
            response['description'] = question.description
            response['anonymous'] = question.anonymous
            response['edited'] = question.edited

            if question.anonymous:
                response['user_id'] = question.user.pk
                response['user'] = 'Anonymous User(self)'
            elif question.user:
                response['user_id'] = question.user.pk
                user_data = models.InstituteStudents.objects.filter(
                    invitee__pk=question.user.pk,
                    institute__pk=institute.pk
                ).only('first_name', 'last_name').first()
                if user_data.first_name and user_data.last_name:
                    response['user'] = user_data.first_name + ' ' + user_data.last_name
                else:
                    response['user'] = str(question.user)

            response['upvotes'] = models.InstituteSubjectCourseContentQuestionUpvote.objects.filter(
                course_content_question__pk=question.pk
            ).count()
            response['answer_count'] = models.InstituteSubjectCourseContentAnswer.objects.filter(
                content_question__pk=question.pk
            ).count()
            response['upvoted'] = models.InstituteSubjectCourseContentQuestionUpvote.objects.filter(
                course_content_question__pk=question.pk,
                user=self.request.user
            ).exists()

            return Response(response, status=status.HTTP_200_OK)

        except Exception:
            return Response({'error': _('Internal server error.')},
                            status=status.HTTP_400_BAD_REQUEST)


class InstituteSubjectCourseContentPinUnpinAnswerView(APIView):
    """View for pinning answer by asker of question"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacherOrStudent)

    def patch(self, request, *args, **kwargs):
        answer = models.InstituteSubjectCourseContentAnswer.objects.filter(
            pk=kwargs.get('answer_pk')
        ).only('pin').first()

        if not answer:
            return Response({'error': _('Answer may have been deleted.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if answer.content_question.user.pk != self.request.user.pk:
            return Response({'error': _('Permission denied.')},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            pin_status = None
            if 'pin' in request.data:
                answer.pin = True
                pin_status = 'PINNED'
            else:
                answer.pin = False
                pin_status = 'UNPINNED'
            answer.save()
            return Response({'status': pin_status},
                            status=status.HTTP_200_OK)
        except IntegrityError as e:
            if 'unique_pinned_answer_constraint' in str(e):
                return Response({'error': _('Error! You can not pin more than 1 answer.')},
                                status=status.HTTP_400_BAD_REQUEST)
            else:
                return Response({'error': _('Error! Could not pin answer.')},
                                status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            return Response({'error': _('Internal server error.')},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class InstituteSubjectCourseQuestionListAnswerView(APIView):
    """View for listing answer by permitted student, instructor and admin"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacherOrStudent)

    def get(self, *args, **kwargs):
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug').lower()
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug').lower()
        ).only('institute_slug').first()

        if not institute:
            return Response({'error': _('Institute not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectStudents.objects.filter(
            institute_subject=subject,
            institute_student__invitee=self.request.user,
            active=True,
            is_banned=False
        ).exists():
            if not models.InstituteSubjectPermission.objects.filter(
                to=subject,
                invitee=self.request.user
            ).exists() and not models.InstitutePermission.objects.filter(
                institute=institute,
                invitee=self.request.user,
                active=True,
                role=models.InstituteRole.ADMIN
            ).exists():
                return Response({'error': _('Permission denied.')},
                                status=status.HTTP_400_BAD_REQUEST)

        if not get_active_common_license(institute):
            return Response({'error': _('Active license not found or expired.')},
                            status=status.HTTP_400_BAD_REQUEST)

        question = models.InstituteSubjectCourseContentQuestions.objects.filter(
            pk=kwargs.get('question_pk')
        ).only('rgb_color').first()

        if not question:
            return Response({'error': _('Question may have been deleted.')},
                            status=status.HTTP_400_BAD_REQUEST)

        answers = models.InstituteSubjectCourseContentAnswer.objects.filter(
            content_question=question
        ).order_by('-created_on').order_by('-pin')

        try:
            response = list()
            for ans in answers:
                res = dict()
                res['id'] = ans.pk
                res['created_on'] = ans.created_on
                res['pin'] = ans.pin
                res['anonymous'] = ans.anonymous
                res['rgb_color'] = ans.rgb_color
                res['answer'] = ans.answer
                res['edited'] = ans.edited

                if ans.anonymous:
                    if ans.user and ans.user.pk == self.request.user.pk:
                        res['user_id'] = ans.user.pk
                        res['user'] = 'Anonymous User(self)'
                    else:
                        res['user'] = 'Anonymous User'

                elif ans.user:
                    res['user_id'] = ans.user.pk
                    is_student = models.InstituteStudents.objects.filter(
                        invitee__pk=ans.user.pk
                    ).exists()
                    user_data = None

                    if is_student:
                        user_data = models.InstituteStudents.objects.filter(
                            invitee__pk=ans.user.pk,
                            institute=institute
                        ).only('first_name', 'last_name').first()
                    else:
                        user_data = models.UserProfile.objects.filter(
                            user__pk=ans.user.pk
                        ).only('first_name', 'last_name').first()

                    if user_data and user_data.first_name and user_data.last_name:
                        res['user'] = user_data.first_name + ' ' + user_data.last_name
                    else:
                        res['user'] = str(ans.user)
                else:
                    res['user'] = 'Deleted User'

                res['upvotes'] = models.InstituteSubjectCourseContentAnswerUpvote.objects.filter(
                    course_content_answer__pk=ans.pk
                ).count()
                res['upvoted'] = models.InstituteSubjectCourseContentAnswerUpvote.objects.filter(
                    course_content_answer__pk=ans.pk,
                    user=self.request.user
                ).exists()
                response.append(res)

            return Response(response, status=status.HTTP_200_OK)
        except Exception:
            return Response({'error': _('Internal server error occurred.')},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class InstituteSubjectCourseListQuestionView(APIView):
    """View for listing question by instructor, admin and permitted student"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacherOrStudent)

    def get(self, *args, **kwargs):
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug').lower()
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug').lower()
        ).only('institute_slug').first()

        if not institute:
            return Response({'error': _('Institute not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectStudents.objects.filter(
                institute_subject=subject,
                institute_student__invitee=self.request.user,
                active=True,
                is_banned=False
        ).exists():
            if not models.InstituteSubjectPermission.objects.filter(
                    to=subject,
                    invitee=self.request.user
            ).exists() and not models.InstitutePermission.objects.filter(
                institute=institute,
                invitee=self.request.user,
                active=True,
                role=models.InstituteRole.ADMIN
            ).exists():
                return Response({'error': _('Permission denied.')},
                                status=status.HTTP_400_BAD_REQUEST)

        if not get_active_common_license(institute):
            return Response({'error': _('Active license not found or expired.')},
                            status=status.HTTP_400_BAD_REQUEST)

        course_content = models.InstituteSubjectCourseContent.objects.filter(
            pk=kwargs.get('course_content_pk')
        ).only('order').first()

        if not course_content:
            return Response({'error': _('This study material may have been deleted.')},
                            status=status.HTTP_400_BAD_REQUEST)

        questions = models.InstituteSubjectCourseContentQuestions.objects.filter(
            course_content=course_content
        ).order_by('-created_on')
        response = list()
        for q in questions:
            res = dict()
            res['id'] = q.id
            res['question'] = q.question
            res['rgb_color'] = q.rgb_color
            res['created_on'] = q.created_on
            res['description'] = q.description
            res['anonymous'] = q.anonymous
            res['edited'] = q.edited

            if q.anonymous:
                if q.user and q.user.pk == self.request.user.pk:
                    res['user_id'] = q.user.pk
                    res['user'] = 'Anonymous User(self)'
                else:
                    res['user'] = 'Anonymous User'
            elif q.user:
                res['user_id'] = q.user.pk
                user_data = models.InstituteStudents.objects.filter(
                    invitee__pk=q.user.pk,
                    institute__pk=institute.pk
                ).only('first_name', 'last_name').first()
                if user_data.first_name and user_data.last_name:
                    res['user'] = user_data.first_name + ' ' + user_data.last_name
                else:
                    res['user'] = str(q.user)
            else:
                res['user'] = 'Deleted User'

            res['upvotes'] = models.InstituteSubjectCourseContentQuestionUpvote.objects.filter(
                course_content_question__pk=q.pk
            ).count()
            res['answer_count'] = models.InstituteSubjectCourseContentAnswer.objects.filter(
                content_question__pk=q.pk
            ).count()
            res['upvoted'] = models.InstituteSubjectCourseContentQuestionUpvote.objects.filter(
                course_content_question__pk=q.pk,
                user=self.request.user
            ).exists()
            response.append(res)

        return Response(response, status=status.HTTP_200_OK)


class GetUserProfileDetailsOfInstituteView(APIView):
    """View to get name, gender, and date of birth of student in institute"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsStudent)

    def get(self, *args, **kwargs):
        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug')
        ).first()

        if not institute:
            return Response({'error': _('This institute does not exist.')},
                            status=status.HTTP_400_BAD_REQUEST)

        invitation = models.InstituteStudents.objects.filter(
            institute=institute,
            invitee=self.request.user
        ).only('first_name', 'last_name', 'gender', 'date_of_birth').first()

        if not invitation:
            return Response({'error': _('Invitation may have been deleted.')},
                            status=status.HTTP_400_BAD_REQUEST)

        user_profile_data = models.UserProfile.objects.filter(
            user=self.request.user
        ).only('first_name', 'last_name', 'gender', 'date_of_birth').first()

        response = dict()

        if invitation.first_name and invitation.last_name:
            response['first_name'] = invitation.first_name
            response['last_name'] = invitation.last_name
        else:
            response['first_name'] = user_profile_data.first_name
            response['last_name'] = user_profile_data.last_name

        if invitation.gender:
            response['gender'] = invitation.gender
        else:
            response['gender'] = user_profile_data.gender

        if invitation.date_of_birth:
            response['date_of_birth'] = invitation.date_of_birth
        elif user_profile_data.date_of_birth:
            response['date_of_birth'] = user_profile_data.date_of_birth
        else:
            response['date_of_birth'] = ''

        return Response(response, status=status.HTTP_200_OK)


class StudentJoinInstituteView(APIView):
    """View for student to join institute"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsStudent)

    def post(self, request, *args, **kwargs):
        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug')
        ).only('institute_slug').first()

        if not institute:
            return Response({'error': _('This institute does not exist.')},
                            status=status.HTTP_400_BAD_REQUEST)

        institute_student = models.InstituteStudents.objects.filter(
            institute=institute,
            invitee=self.request.user
        ).only('first_name', 'last_name', 'gender', 'date_of_birth', 'active', 'edited').first()

        if not institute_student:
            return Response({'error': _('Invitation may have been deleted.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if institute_student.invitee != self.request.user:
            return Response({'error': _('Permission denied.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not institute_student.edited:
            institute_student.first_name = request.data.get('first_name')
            institute_student.last_name = request.data.get('last_name')
            institute_student.gender = request.data.get('gender')
            institute_student.date_of_birth = request.data.get('date_of_birth')

        institute_student.active = True
        institute_student.edited = True
        institute_student.save()

        assigned_class_invite = models.InstituteClassStudents.objects.filter(
            institute_student=institute_student,
            institute_class__class_institute__pk=institute.pk
        ).only('active').first()

        if assigned_class_invite:
            assigned_class_invite.active = True
            assigned_class_invite.save()

            subjects = models.InstituteSubjectStudents.objects.filter(
                institute_student=institute_student,
                institute_subject__subject_class__pk=assigned_class_invite.institute_class.pk
            ).only('active')

            for subject in subjects:
                subject.active = True
                subject.save()

        return Response({'status': 'OK'}, status.HTTP_200_OK)


#####################################################
# Institute Subject Test
#####################################################
class InstituteSubjectAddTestView(APIView):
    """View for adding test in institute"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def post(self, request, *args, **kwargs):
        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug')
        ).only('institute_slug').first()

        if not institute:
            return Response({'error': _('Institute not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug')
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectPermission.objects.filter(
            to=subject,
            invitee=self.request.user
        ).exists():
            return Response({'error': _('Permission denied.')},
                            status=status.HTTP_400_BAD_REQUEST)

        license_ = get_active_common_license(institute)

        if not license_:
            return Response({'error': _('Institute license not found or expired.')},
                            status=status.HTTP_400_BAD_REQUEST)

        lecture = None
        view = None

        if request.data.get('test_place') == models.TestPlace.LECTURE:
            lecture = models.SubjectLecture.objects.filter(
                pk=request.data.get('lecture_id')
            ).only('id').first()

            if not lecture:
                return Response({'error': _('Lecture not found or may have been deleted.')},
                                status=status.HTTP_400_BAD_REQUEST)
        elif request.data.get('test_place') == models.TestPlace.MODULE:
            view = models.SubjectViewNames.objects.filter(
                key=request.data.get('view_key')
            ).only('id').first()

            if not view:
                return Response({'error': _('Module not found or may have been deleted.')},
                                status=status.HTTP_400_BAD_REQUEST)
        else:
            view = models.SubjectViewNames.objects.create(
                view_subject=subject,
                name=request.data.get('name'),
                type=models.SubjectViewType.TEST_VIEW
            )

        test = None
        try:
            test = models.SubjectTest.objects.create(
                subject=subject,
                lecture=lecture,
                view=view,
                test_place=request.data.get('test_place'),
                name=request.data.get('name'),
                type=request.data.get('type'),
                total_marks=request.data.get('total_marks'),
                total_duration=request.data.get('total_duration'),
                test_schedule_type=request.data.get('test_schedule_type'),
                test_schedule=request.data.get('test_schedule'),
                instruction=request.data.get('instruction'),
                no_of_optional_section_answer=request.data.get('no_of_optional_section_answer'),
                question_mode=request.data.get('question_mode'),
                answer_mode=request.data.get('answer_mode'),
                question_category=request.data.get('question_category'),
                no_of_attempts=request.data.get('no_of_attempts'),
                publish_result_automatically=request.data.get('publish_result_automatically'),
                enable_peer_check=request.data.get('enable_peer_check'),
                allow_question_preview_10_min_before=request.data.get('allow_question_preview_10_min_before'),
                shuffle_questions=request.data.get('shuffle_questions')
            )
            response = dict()

            if request.data.get('test_place') == models.TestPlace.MODULE:
                module_view = models.SubjectModuleView.objects.create(
                    view=view,
                    type=models.SubjectModuleViewType.TEST_VIEW,
                    test=test
                )
                response['module_view_id'] = module_view.pk
                response['type'] = models.SubjectModuleViewType.TEST_VIEW

            response['test_id'] = test.id
            response['test_slug'] = test.test_slug
            response['name'] = test.name
            response['question_mode'] = test.question_mode
            response['test_schedule'] = test.test_schedule
            response['test_schedule_type'] = test.test_schedule_type
            response['test_place'] = test.test_place
            response['test_type'] = test.type
            response['test_live'] = test.test_live

            if test.lecture:
                response['lecture_id'] = test.lecture.pk

            if test.view:
                response['view_key'] = view.key

            return Response(response, status=status.HTTP_201_CREATED)

        except ValueError as e:
            if test:
                test.delete()
            return Response({'error': str(e)},
                            status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            print(e)
            return Response({'error': _('Unhandled internal server error occurred.')},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class InstituteTestMinDetailsView(APIView):
    """View for getting min details of test"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def get(self, *args, **kwargs):
        """Only subject, class incharge and admin can view."""
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug')
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        perm_type = None
        # Checking permission
        if models.InstituteSubjectPermission.objects.filter(
            to=subject,
            invitee=self.request.user
        ).exists():
            perm_type = models.PermissionType.ROLE_BASED
        else:
            if models.InstituteClassPermission.objects.filter(
                to__pk=subject.subject_class.pk,
                invitee=self.request.user
            ).exists():
                perm_type = models.PermissionType.VIEW_ONLY
            else:
                institute = models.Institute.objects.filter(
                    institute_slug=kwargs.get('institute_slug')
                ).only('institute_slug').first()

                if not institute:
                    return Response({'error': _('Institute not found.')},
                                    status=status.HTTP_400_BAD_REQUEST)

                if models.InstitutePermission.objects.filter(
                    institute=institute,
                    invitee=self.request.user,
                    role=models.InstituteRole.ADMIN,
                    active=True
                ).exists():
                    perm_type = models.PermissionType.VIEW_ONLY
                else:
                    return Response({'error': _('Permission denied.')},
                                    status=status.HTTP_400_BAD_REQUEST)

        # Getting test details
        test = models.SubjectTest.objects.filter(
            test_slug=kwargs.get('test_slug')
        ).only('question_mode', 'question_category').first()

        return Response({
            'question_mode': test.question_mode,
            'perm_type': perm_type,
            'question_category': test.question_category
        }, status=status.HTTP_200_OK)


class InstituteTestFullDetailsView(APIView):
    """View for getting full details of test"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def get(self, *args, **kwargs):
        """Only subject, class incharge and admin can view."""
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug')
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug')
        ).only('institute_slug').first()

        if not institute:
            return Response({'error': _('Institute not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        perm_type = None
        # Checking permission
        if models.InstituteSubjectPermission.objects.filter(
            to=subject,
            invitee=self.request.user
        ).exists():
            perm_type = models.PermissionType.ROLE_BASED
        else:
            if models.InstituteClassPermission.objects.filter(
                to__pk=subject.subject_class.pk,
                invitee=self.request.user
            ).exists():
                perm_type = models.PermissionType.VIEW_ONLY
            else:
                if models.InstitutePermission.objects.filter(
                    institute=institute,
                    invitee=self.request.user,
                    role=models.InstituteRole.ADMIN,
                    active=True
                ).exists():
                    perm_type = models.PermissionType.VIEW_ONLY
                else:
                    return Response({'error': _('Permission denied.')},
                                    status=status.HTTP_400_BAD_REQUEST)

        # Getting license details
        if not get_active_common_license(institute):
            return Response({'error': _('Active LMS CMS license not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        # Getting test details
        test = models.SubjectTest.objects.filter(
            test_slug=kwargs.get('test_slug')
        ).only('test_slug', 'question_mode', 'question_category',
               'name', 'type', 'total_marks', 'total_duration',
               'test_schedule_type', 'test_schedule', 'instruction',
               'no_of_optional_section_answer', 'no_of_attempts',
               'publish_result_automatically', 'enable_peer_check',
               'allow_question_preview_10_min_before', 'shuffle_questions',
               'result_published', 'test_live').first()

        return Response({
            'question_mode': test.question_mode,
            'perm_type': perm_type,
            'test_id': test.pk,
            'test_slug': test.test_slug,
            'question_category': test.question_category,
            'name': test.name,
            'type': test.type,
            'total_marks': test.total_marks,
            'total_duration': test.total_duration,
            'test_schedule_type': test.test_schedule_type,
            'test_schedule': test.test_schedule,
            'instruction': test.instruction,
            'no_of_optional_section_answer': test.no_of_optional_section_answer,
            'no_of_attempts': test.no_of_attempts,
            'publish_result_automatically': test.publish_result_automatically,
            'enable_peer_check': test.enable_peer_check,
            'allow_question_preview_10_min_before': test.allow_question_preview_10_min_before,
            'shuffle_questions': test.shuffle_questions,
            'result_published': test.result_published,
            'test_live': test.test_live,
            'subject_name': test.subject.name,
            'class_name': test.subject.subject_class.name
        }, status=status.HTTP_200_OK)


class InstituteTestMinDetailsForQuestionCreationView(APIView):
    """View for getting min details of test for question paper creation"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def get(self, *args, **kwargs):
        """Only subject in-charge can access."""
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug')
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectPermission.objects.filter(
            to=subject,
            invitee=self.request.user
        ).exists():
            return Response({'error': _('Permission denied [Subject in-charge only]')},
                            status=status.HTTP_400_BAD_REQUEST)

        # Getting test details
        test = models.SubjectTest.objects.filter(
            test_slug=kwargs.get('test_slug')
        ).only('question_mode').first()

        response = {
            'name': test.name,
            'type': test.type,
            'total_marks': test.total_marks,
            'total_duration': test.total_duration,
            'test_schedule_type': test.test_schedule_type,
            'instruction': test.instruction,
            'test_live': test.test_live,
            'subject_name': test.subject.name,
            'class_name': test.subject.subject_class.name,
            'test_sets': list()
        }

        if test.test_schedule_type != models.TestScheduleType.UNSCHEDULED:
            response['test_schedule'] = test.test_schedule

        if test.question_mode != models.QuestionMode.FILE:
            response['no_of_optional_section_answer'] = test.no_of_optional_section_answer
            response['question_category'] = test.question_category

        # Getting test labels
        if test.question_mode != models.QuestionMode.FILE:
            response['labels'] = list()
            for label in models.SubjectTestConceptLabels.objects.filter(
                test=test
            ):
                response['labels'].append({
                    'id': label.pk,
                    'name': label.name
                })

        # Getting questions of first test set (if any)
        for ts in models.SubjectTestSets.objects.filter(
            test=test
        ).order_by('created_on'):
            response['test_sets'].append({
                'id': ts.pk,
                'set_name': ts.set_name,
                'verified': ts.verified,
                'active': ts.active,
                'mark_as_final': ts.mark_as_final,
                'created_on': ts.created_on
            })

        if len(response['test_sets']) > 0:
            # Find the questions of first set
            # File type question paper
            if test.question_mode == models.QuestionMode.FILE:
                question_set = models.SubjectFileTestQuestion.objects.filter(
                    test=test,
                    set__pk=response['test_sets'][0]['id']
                ).first()

                if question_set:
                    response['first_set_questions'] = {
                        'id': question_set.pk,
                        'file': self.request.build_absolute_uri('/').strip('/') + MEDIA_URL + '/' + str(question_set.file)
                    }
                else:
                    response['first_set_questions'] = None

            # Image mode or typed question paper
            elif test.question_mode == models.QuestionMode.IMAGE or test.question_mode == models.QuestionMode.TYPED:
                response['first_set_questions'] = list()

                for qs in models.SubjectTestQuestionSection.objects.filter(
                    test=test,
                    set__pk=response['test_sets'][0]['id']
                ).order_by('order'):
                    res = {
                        'section_id': qs.pk,
                        'name': qs.name,
                        'order': qs.order,
                        'view': qs.view,
                        'no_of_question_to_attempt': qs.no_of_question_to_attempt,
                        'answer_all_questions': qs.answer_all_questions,
                        'section_mandatory': qs.section_mandatory
                    }
                    questions = list()

                    if test.question_mode == models.QuestionMode.IMAGE:
                        for q in models.SubjectPictureTestQuestion.objects.filter(
                            test_section__pk=qs.pk
                        ).order_by('order'):
                            question_data = {
                                'question_id': q.pk,
                                'order': q.order,
                                'text': q.text,
                                'marks': q.marks,
                                'file': self.request.build_absolute_uri('/').strip('/') + MEDIA_URL + '/' + str(q.file)
                            }
                            if q.concept_label:
                                question_data['concept_label_id'] = q.concept_label.pk
                            questions.append(question_data)
                    else:
                        # Find typed test questions
                        for q in models.SubjectTypedTestQuestion.objects.filter(
                            test_section__pk=qs.pk
                        ).order_by('order'):
                            question_data = {
                                'question_id': q.pk,
                                'order': q.order,
                                'question': q.question,
                                'marks': q.marks,
                                'has_picture': q.has_picture,
                                'type': q.type
                            }

                            if q.concept_label:
                                question_data['concept_label_id'] = q.concept_label.pk

                            if q.has_picture:
                                picture = models.SubjectTestQuestionImage.objects.filter(
                                    question__pk=q.pk
                                ).first()

                                if picture:
                                    question_data['image'] = self.request.build_absolute_uri(
                                        '/').strip('/') + MEDIA_URL + '/' + str(picture.file)

                            # Find data according to question type
                            if q.type == models.QuestionType.MCQ:
                                question_data['options'] = list()

                                for option in models.SubjectTestMcqOptions.objects.filter(
                                        question__pk=q.pk
                                ):
                                    question_data['options'].append({
                                        'option_id': option.pk,
                                        'option': option.option,
                                        'correct_answer': option.correct_answer
                                    })

                            elif q.type == models.QuestionType.SELECT_MULTIPLE_CHOICE:
                                question_data['options'] = list()
                                for option in models.SubjectTestSelectMultipleCorrectAnswer.objects.filter(
                                        question__pk=q.pk
                                ):
                                    question_data['options'].append({
                                        'option_id': option.pk,
                                        'option': option.option,
                                        'correct_answer': option.correct_answer
                                    })

                            elif q.type == models.QuestionType.TRUE_FALSE:
                                true_false_correct_answer = models.SubjectTestTrueFalseCorrectAnswer.objects.filter(
                                    question__pk=q.pk
                                ).first()

                                if true_false_correct_answer:
                                    question_data['correct_answer'] = true_false_correct_answer.correct_answer

                            elif q.type == models.QuestionType.ASSERTION:
                                assertion_correct_answer = models.SubjectTestAssertionCorrectAnswer.objects.filter(
                                    question__pk=q.pk
                                ).first()

                                if assertion_correct_answer:
                                    question_data['correct_answer'] = assertion_correct_answer.correct_answer

                            elif q.type == models.QuestionType.NUMERIC_ANSWER:
                                numeric_correct_answer = models.SubjectTestNumericCorrectAnswer.objects.filter(
                                    question__pk=q.pk
                                ).first()

                                if numeric_correct_answer:
                                    question_data['correct_answer'] = numeric_correct_answer.correct_answer

                            elif q.type == models.QuestionType.FILL_IN_THE_BLANK:
                                fill_in_the_blank = models.SubjectTestFillInTheBlankCorrectAnswer.objects.filter(
                                    question__pk=q.pk
                                ).first()

                                if fill_in_the_blank:
                                    question_data['fill_in_the_blank_answer'] = {
                                        'correct_answer': fill_in_the_blank.correct_answer,
                                        'manual_checking': fill_in_the_blank.manual_checking,
                                        'enable_strict_checking': fill_in_the_blank.enable_strict_checking,
                                        'ignore_grammar': fill_in_the_blank.ignore_grammar,
                                        'ignore_special_characters': fill_in_the_blank.ignore_special_characters
                                    }

                            questions.append(question_data)

                    res['questions'] = questions
                    response['first_set_questions'].append(res)

        return Response(response, status=status.HTTP_200_OK)


class InstituteGetQuestionSetQuestionsView(APIView):
    """View for getting question set questions"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def get(self, *args, **kwargs):
        """Subject incharge, class incharge, admin can view."""
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug')
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug')
        ).only('institute_slug').first()

        if not institute:
            return Response({'error': _('Institute not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectPermission.objects.filter(
            to=subject,
            invitee=self.request.user
        ).exists():
            if not models.InstituteClassPermission.objects.filter(
                to__pk=subject.subject_class.pk,
                invitee=self.request.user
            ).exists():
                if not models.InstitutePermission.objects.filter(
                    institute=institute,
                    invitee=self.request.user,
                    role=models.InstituteRole.Admin,
                    active=True
                ).exists():
                    return Response({'error': _('Permission denied [Subject, Class in-charge, Admin, only]')},
                                    status=status.HTTP_400_BAD_REQUEST)

        if not get_active_or_expired_common_license(institute):
            return Response({'error': _('LMS CMS license not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        test = models.SubjectTest.objects.filter(
            test_slug=kwargs.get('test_slug'),
            subject=subject
        ).only('question_mode').first()

        set_ = models.SubjectTestSets.objects.filter(
            pk=kwargs.get('set_id'),
            test=test
        ).only('set_name').first()

        if not set_:
            return Response({'error': _('Question set not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if test.question_mode == models.QuestionMode.FILE:
            # Find file test questions
            response = dict()
            question_set = models.SubjectFileTestQuestion.objects.filter(
                test=test,
                set=set_
            ).first()

            if question_set:
                response.update({
                    'id': question_set.pk,
                    'file': self.request.build_absolute_uri('/').strip('/') + MEDIA_URL + '/' + str(question_set.file)
                })
            else:
                response = None
        elif test.question_mode == models.QuestionMode.IMAGE or test.question_mode == models.QuestionMode.TYPED:
            response = list()

            for qs in models.SubjectTestQuestionSection.objects.filter(
                test=test,
                set=set_
            ).order_by('order'):
                res = {
                    'section_id': qs.pk,
                    'name': qs.name,
                    'order': qs.order,
                    'view': qs.view,
                    'no_of_question_to_attempt': qs.no_of_question_to_attempt,
                    'answer_all_questions': qs.answer_all_questions,
                    'section_mandatory': qs.section_mandatory
                }
                questions = list()

                if test.question_mode == models.QuestionMode.IMAGE:
                    # Find image test questions
                    for q in models.SubjectPictureTestQuestion.objects.filter(
                            test_section__pk=qs.pk
                    ).order_by('order'):
                        question_data = {
                            'question_id': q.pk,
                            'order': q.order,
                            'text': q.text,
                            'marks': q.marks,
                            'file': self.request.build_absolute_uri('/').strip('/') + MEDIA_URL + '/' + str(q.file)
                        }
                        if q.concept_label:
                            question_data['concept_label_id'] = q.concept_label.pk
                        questions.append(question_data)
                else:
                    # Find typed test questions
                    for q in models.SubjectTypedTestQuestion.objects.filter(
                            test_section__pk=qs.pk
                    ).order_by('order'):
                        question_data = {
                            'question_id': q.pk,
                            'order': q.order,
                            'question': q.question,
                            'marks': q.marks,
                            'type': q.type,
                            'has_picture': q.has_picture
                        }

                        if q.concept_label:
                            question_data['concept_label_id'] = q.concept_label.pk

                        if q.has_picture:
                            picture = models.SubjectTestQuestionImage.objects.filter(
                                question__pk=q.pk
                            ).first()
                            if picture:
                                question_data['image'] = self.request.build_absolute_uri(
                                    '/').strip('/') + MEDIA_URL + '/' + str(picture.file)

                        # Find appropriate options according to question
                        if q.type == models.QuestionType.MCQ:
                            question_data['options'] = list()
                            for option in models.SubjectTestMcqOptions.objects.filter(
                                question__pk=q.pk
                            ):
                                question_data['options'].append({
                                    'option_id': option.pk,
                                    'option': option.option,
                                    'correct_answer': option.correct_answer
                                })

                        elif q.type == models.QuestionType.TRUE_FALSE:
                            true_false_correct_answer = models.SubjectTestTrueFalseCorrectAnswer.objects.filter(
                                question__pk=q.pk
                            ).first()

                            if true_false_correct_answer:
                                question_data['correct_answer'] = true_false_correct_answer.correct_answer

                        elif q.type == models.QuestionType.ASSERTION:
                            assertion_correct_answer = models.SubjectTestAssertionCorrectAnswer.objects.filter(
                                question__pk=q.pk
                            ).first()

                            if assertion_correct_answer:
                                question_data['correct_answer'] = assertion_correct_answer.correct_answer

                        elif q.type == models.QuestionType.SELECT_MULTIPLE_CHOICE:
                            question_data['options'] = list()
                            for option in models.SubjectTestSelectMultipleCorrectAnswer.objects.filter(
                                    question__pk=q.pk
                            ):
                                question_data['options'].append({
                                    'option_id': option.pk,
                                    'option': option.option,
                                    'correct_answer': option.correct_answer
                                })

                        elif q.type == models.QuestionType.NUMERIC_ANSWER:
                            numeric_correct_answer = models.SubjectTestNumericCorrectAnswer.objects.filter(
                                question__pk=q.pk
                            ).first()

                            if numeric_correct_answer:
                                question_data['correct_answer'] = numeric_correct_answer.correct_answer

                        elif q.type == models.QuestionType.FILL_IN_THE_BLANK:
                            fill_in_the_blank = models.SubjectTestFillInTheBlankCorrectAnswer.objects.filter(
                                question__pk=q.pk
                            ).first()

                            if fill_in_the_blank:
                                question_data['fill_in_the_blank_answer'] = {
                                    'correct_answer': fill_in_the_blank.correct_answer,
                                    'manual_checking': fill_in_the_blank.manual_checking,
                                    'enable_strict_checking': fill_in_the_blank.enable_strict_checking,
                                    'ignore_grammar': fill_in_the_blank.ignore_grammar,
                                    'ignore_special_characters': fill_in_the_blank.ignore_special_characters
                                }

                        questions.append(question_data)

                res['questions'] = questions
                response.append(res)

        return Response(response, status=status.HTTP_200_OK)


class InstituteAddQuestionSetView(APIView):
    """View for adding question set"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def post(self, request, *args, **kwargs):
        """Only subject in-charge can access."""
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug')
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectPermission.objects.filter(
            to=subject,
            invitee=self.request.user
        ).exists():
            return Response({'error': _('Permission denied [Subject in-charge only]')},
                            status=status.HTTP_400_BAD_REQUEST)

        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug')
        ).only('institute_slug').first()

        if not institute:
            return Response({'error': _('Institute not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not get_active_common_license(institute):
            return Response({'error': _('Institute LMS CMS license expired or not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        test = models.SubjectTest.objects.filter(
            test_slug=kwargs.get('test_slug')
        ).only('type').first()

        try:
            test_set = models.SubjectTestSets.objects.create(
                set_name=request.data.get('set_name'),
                test=test)
        except IntegrityError as e:
            if 'same_set_name' in str(e):
                return Response({'error': _('Error! Same question set exists for this test.')},
                                status=status.HTTP_400_BAD_REQUEST)
            else:
                return Response({'error': _('Unhandled error occurred.')},
                                status=status.HTTP_400_BAD_REQUEST)

        return Response({
            'id': test_set.pk,
            'set_name': test_set.set_name,
            'verified': test_set.verified,
            'active': test_set.active,
            'mark_as_final': test_set.mark_as_final,
            'created_on': test_set.created_on
        }, status=status.HTTP_201_CREATED)


class InstituteUploadFileQuestionPaperView(APIView):
    """View for adding file question paper"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def post(self, request, *args, **kwargs):
        """Only subject in-charge can access."""
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug')
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectPermission.objects.filter(
            to=subject,
            invitee=self.request.user
        ).exists():
            return Response({'error': _('Permission denied [Subject in-charge only]')},
                            status=status.HTTP_400_BAD_REQUEST)

        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug')
        ).only('institute_slug').first()

        if not institute:
            return Response({'error': _('Institute not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not get_active_common_license(institute):
            return Response({'error': _('Institute LMS CMS license expired or not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        institute_license_stats = models.InstituteLicenseStat.objects.filter(
            institute=institute
        ).only('total_storage').first()

        if not institute_license_stats.total_storage:
            return Response({'error': _('Storage license expired or not found. Purchase storage to upload files.')},
                            status=status.HTTP_400_BAD_REQUEST)

        pdf_error = validate_pdf_file(request.data.get('file'), str(request.FILES['file']))

        if pdf_error:
            return pdf_error

        institute_stats = models.InstituteStatistics.objects.filter(
            institute=institute
        ).only('storage').first()

        if request.data.get('file').size / 1000000000 +\
                float(institute_stats.storage) > float(institute_license_stats.total_storage):
            return Response({'error': _('File size too large. Purchase additional storage to upload files.')},
                            status=status.HTTP_400_BAD_REQUEST)

        test = models.SubjectTest.objects.filter(
            test_slug=kwargs.get('test_slug'),
            subject=subject
        ).only('pk').first()

        if not test:
            return Response({'error': _('Test not found.')},
                            status.HTTP_400_BAD_REQUEST)

        test_set = models.SubjectTestSets.objects.filter(
            pk=kwargs.get('set_id'),
            test=test,
        ).only('mark_as_final').first()

        if not test_set:
            return Response({'error': _('Question paper set not found.')},
                            status.HTTP_400_BAD_REQUEST)

        if test_set.mark_as_final:
            return Response({'error': _('Question set is MARKED AS FINAL. Uploading question is not allowed.')},
                            status.HTTP_400_BAD_REQUEST)

        try:
            ser = serializer.SubjectTestFileQuestionPaperUploadSerializer(
                data={
                    'file': request.data.get('file'),
                    'test': test.pk,
                    'set': test_set.pk
                }, context={'request': request}
            )
            if ser.is_valid():
                ser.save()

                file_size = request.data.get('file').size / 1000000000  # Size in GB

                institute_stats.storage = Decimal(float(institute_stats.storage) + file_size)
                institute_stats.save()

                models.InstituteSubjectStatistics.objects.filter(
                    statistics_subject=subject
                ).update(storage=F('storage') + Decimal(file_size))

                return Response({
                    'id': ser.data['id'],
                    'file': ser.data['file']
                }, status=status.HTTP_201_CREATED)
            else:
                return Response(ser.errors, status=status.HTTP_400_BAD_REQUEST)
        except IntegrityError as e:
            if 'unique_question_paper_for_single_question_set' in str(e):
                return Response({'error': _('Question paper for this question set already uploaded. Please refresh.')},
                                status=status.HTTP_400_BAD_REQUEST)
            else:
                return Response({'error': _('Unhandled error occurred.')},
                                status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        except Exception:
            return Response({'error': _('Unhandled error occurred.')},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class InstituteUploadImageQuestionView(APIView):
    """View for adding image question paper"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def post(self, request, *args, **kwargs):
        """Only subject in-charge can access."""
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug')
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectPermission.objects.filter(
            to=subject,
            invitee=self.request.user
        ).exists():
            return Response({'error': _('Permission denied [Subject in-charge only]')},
                            status=status.HTTP_400_BAD_REQUEST)

        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug')
        ).only('institute_slug').first()

        if not institute:
            return Response({'error': _('Institute not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not get_active_common_license(institute):
            return Response({'error': _('Institute LMS CMS license expired or not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        institute_license_stats = models.InstituteLicenseStat.objects.filter(
            institute=institute
        ).only('total_storage').first()

        if not institute_license_stats.total_storage:
            return Response({'error': _('Storage license expired or not found. Purchase storage to upload files.')},
                            status=status.HTTP_400_BAD_REQUEST)

        image_error = validate_image_file(request.data.get('file'))

        if image_error:
            return image_error

        institute_stats = models.InstituteStatistics.objects.filter(
            institute=institute
        ).only('storage').first()

        if request.data.get('file').size / 1000000000 +\
                float(institute_stats.storage) > float(institute_license_stats.total_storage):
            return Response({'error': _('File size too large. Purchase additional storage to upload files.')},
                            status=status.HTTP_400_BAD_REQUEST)

        test = models.SubjectTest.objects.filter(
            test_slug=kwargs.get('test_slug'),
            subject=subject
        ).only('pk').first()

        if not test:
            return Response({'error': _('Test not found.')},
                            status.HTTP_400_BAD_REQUEST)

        if models.SubjectTestSets.objects.filter(
            pk=kwargs.get('set_id'),
            test=test,
            mark_as_final=True
        ).exists():
            return Response({'error': _('Question set is MARKED AS FINAL. Uploading question is not allowed.')},
                            status.HTTP_400_BAD_REQUEST)

        if not models.SubjectTestQuestionSection.objects.filter(
            pk=kwargs.get('test_section_id'),
            test=test,
        ).exists():
            return Response({'error': _('Question paper group not found.')},
                            status.HTTP_400_BAD_REQUEST)

        if request.data.get('concept_label'):
            if not models.SubjectTestConceptLabels.objects.filter(
                pk=request.data.get('concept_label'),
                test=test
            ).exists():
                return Response({'error': _('Concept label not found. Please refresh.')},
                                status.HTTP_400_BAD_REQUEST)
            data = {
                'file': request.data.get('file'),
                'test': test.pk,
                'test_section': kwargs.get('test_section_id'),
                'text': request.data.get('text'),
                'marks': request.data.get('marks'),
                'concept_label': request.data.get('concept_label')
            }
        else:
            data = {
                'file': request.data.get('file'),
                'test': test.pk,
                'test_section': kwargs.get('test_section_id'),
                'text': request.data.get('text'),
                'marks': request.data.get('marks'),
            }

        try:
            ser = serializer.SubjectTestImageQuestionUploadSerializer(data=data, context={'request': request})

            if ser.is_valid():
                ser.save()

                file_size = request.data.get('file').size / 1000000000  # Size in GB

                institute_stats.storage = Decimal(float(institute_stats.storage) + file_size)
                institute_stats.save()

                models.InstituteSubjectStatistics.objects.filter(
                    statistics_subject=subject
                ).update(storage=F('storage') + Decimal(file_size))
                
                response = {
                    'question_id': ser.data['id'],
                    'file': ser.data['file'],
                    'text': ser.data['text'],
                    'marks': float(ser.data['marks']),
                    'order': ser.data['id']
                }
                if request.data.get('concept_label'):
                    response['concept_label_id'] = ser.data['concept_label']
                return Response(response, status=status.HTTP_201_CREATED)
            else:
                return Response(ser.errors, status=status.HTTP_400_BAD_REQUEST)
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            print(e)
            return Response({'error': _('Unhandled error occurred.')},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class InstituteTypedTestAddQuestionView(APIView):
    """View for adding typed test question"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def post(self, request, *args, **kwargs):
        """Only subject in-charge can access."""
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug')
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectPermission.objects.filter(
                to=subject,
                invitee=self.request.user
        ).exists():
            return Response({'error': _('Permission denied [Subject in-charge only]')},
                            status=status.HTTP_400_BAD_REQUEST)

        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug')
        ).only('institute_slug').first()

        if not institute:
            return Response({'error': _('Institute not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not get_active_common_license(institute):
            return Response({'error': _('Institute LMS CMS license expired or not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        test = models.SubjectTest.objects.filter(
            test_slug=kwargs.get('test_slug'),
            subject=subject
        ).only('question_category').first()

        if not test:
            return Response({'error': _('Test not found.')},
                            status.HTTP_400_BAD_REQUEST)

        if test.question_category == models.QuestionCategory.AUTOCHECK_TYPE:
            if request.data.get('type') != models.QuestionType.MCQ and\
                    request.data.get('type') != models.QuestionType.TRUE_FALSE and\
                    request.data.get('type') != models.QuestionType.SELECT_MULTIPLE_CHOICE and\
                    request.data.get('type') != models.QuestionType.NUMERIC_ANSWER:
                return Response({'error': _('Question type not allowed [ PROGRAMMING ERROR ]')},
                                status=status.HTTP_400_BAD_REQUEST)

        if models.SubjectTestSets.objects.filter(
                pk=kwargs.get('set_id'),
                test=test,
                mark_as_final=True
        ).exists():
            return Response({'error': _('Question set is MARKED AS FINAL. Uploading question is not allowed.')},
                            status.HTTP_400_BAD_REQUEST)

        question_section = models.SubjectTestQuestionSection.objects.filter(
            pk=kwargs.get('test_section_id'),
            test=test,
        ).first()

        if not question_section:
            return Response({'error': _('Question paper group not found.')},
                            status.HTTP_400_BAD_REQUEST)

        concept_label = None

        if request.data.get('concept_label'):
            concept_label = models.SubjectTestConceptLabels.objects.filter(
                pk=request.data.get('concept_label'),
                test=test
            ).only('pk').first()

            if not concept_label:
                return Response({'error': _('Concept label not found. Please refresh.')},
                                status.HTTP_400_BAD_REQUEST)

        try:
            question = models.SubjectTypedTestQuestion.objects.create(
                type=request.data.get('type'),
                question=request.data.get('question'),
                marks=request.data.get('marks'),
                has_picture=request.data.get('has_picture'),
                test_section=question_section,
                concept_label=concept_label,
            )

            response = {
                'question_id': question.pk,
                'question': question.question,
                'marks': question.marks,
                'order': question.order,
                'has_picture': question.has_picture,
                'type': question.type
            }
            if request.data.get('concept_label'):
                response['concept_label_id'] = question.concept_label.pk

            if question.type == models.QuestionType.SELECT_MULTIPLE_CHOICE or\
                    question.type == models.QuestionType.MCQ:
                response['options'] = list()

            return Response(response, status=status.HTTP_201_CREATED)
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            print(e)
            return Response({'error': _('Unhandled error occurred.')},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class InstituteEditTypedTestQuestionView(APIView):
    """View for editing typed test question paper"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def patch(self, request, *args, **kwargs):
        """Only subject in-charge can access."""
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug')
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug')
        ).only('institute_slug').first()

        if not institute:
            return Response({'error': _('Institute not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectPermission.objects.filter(
            to=subject,
            invitee=self.request.user
        ).exists():
            return Response({'error': _('Permission denied [Subject in-charge only]')},
                            status=status.HTTP_400_BAD_REQUEST)

        question_set = models.SubjectTestSets.objects.filter(
            pk=kwargs.get('set_id'),
            test__test_slug=kwargs.get('test_slug')
        ).only('mark_as_final').first()

        if not question_set:
            return Response({'error': _('Question set not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if question_set.mark_as_final:
            return Response({'error': _('Question set is MARKED AS FINAL. Editing question is not allowed.')},
                            status.HTTP_400_BAD_REQUEST)

        question = models.SubjectTypedTestQuestion.objects.filter(
            pk=kwargs.get('question_id')
        ).first()

        if not question:
            return Response({'error': _('Question not found. Please refresh and try again.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if request.data.get('concept_label'):
            concept_label = models.SubjectTestConceptLabels.objects.filter(
                pk=request.data.get('concept_label')
            ).first()

            if concept_label:
                question.concept_label = concept_label
            else:
                return Response({'error': _('Concept label not found. Please refresh and try again.')},
                                status=status.HTTP_400_BAD_REQUEST)
        else:
            question.concept_label = None

        try:
            if request.data.get('question'):
                question.question = request.data.get('question')
            else:
                question.question = ''

            question.marks = request.data.get('marks')
            question.has_picture = request.data.get('has_picture')
            question.save()

            response = {
                'question_id': question.pk,
                'question': question.question,
                'marks': question.marks,
                'has_picture': question.has_picture,
            }
            if question.concept_label:
                response['concept_label_id'] = question.concept_label.pk

            return Response(response, status=status.HTTP_200_OK)

        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            print(e)
            return Response({'error': _('Unhandled error occurred.')},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class InstituteDeleteTypedQuestionView(APIView):
    """View for deleting typed question paper"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def delete(self, *args, **kwargs):
        """Only subject in-charge can access."""
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug')
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug')
        ).only('institute_slug').first()

        if not institute:
            return Response({'error': _('Institute not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectPermission.objects.filter(
            to=subject,
            invitee=self.request.user
        ).exists():
            return Response({'error': _('Permission denied [Subject in-charge only]')},
                            status=status.HTTP_400_BAD_REQUEST)

        question = models.SubjectTypedTestQuestion.objects.filter(
            pk=kwargs.get('question_id')
        ).first()

        if not question:
            return Response({'error': _('Question not found. Please refresh and try again.')},
                            status=status.HTTP_400_BAD_REQUEST)

        file_size = 0.0

        if question.has_picture:
            picture = models.SubjectTestQuestionImage.objects.filter(
                question=question
            ).first()

            if picture:
                file_size = picture.file.size / 1000000000

        question.delete()

        if file_size > 0.0:
            models.InstituteSubjectStatistics.objects.filter(
                statistics_subject=subject
            ).update(storage=F('storage') - Decimal(file_size))
            models.InstituteStatistics.objects.filter(
                institute=institute
            ).update(storage=F('storage') - Decimal(file_size))

        return Response(status=status.HTTP_204_NO_CONTENT)


class InstituteTestAddUpdateTrueFalseCorrectAnswer(APIView):
    """View for adding true false correct answer"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def post(self, request, *args, **kwargs):
        """Only subject in-charge can access."""
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug')
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectPermission.objects.filter(
                to=subject,
                invitee=self.request.user
        ).exists():
            return Response({'error': _('Permission denied [Subject in-charge only]')},
                            status=status.HTTP_400_BAD_REQUEST)

        question = models.SubjectTypedTestQuestion.objects.filter(
            pk=kwargs.get('question_id')
        ).only('pk').first()

        if not question:
            return Response({'error': _('Question not found.')},
                            status.HTTP_400_BAD_REQUEST)

        try:
            answer = models.SubjectTestTrueFalseCorrectAnswer.objects.filter(
                question=question
            ).first()

            if not answer:
                answer = models.SubjectTestTrueFalseCorrectAnswer.objects.create(
                    question=question,
                    correct_answer=request.data.get('correct_answer')
                )
            else:
                answer.correct_answer = request.data.get('correct_answer')
                answer.save()

            return Response({'correct_answer': answer.correct_answer},
                            status=status.HTTP_201_CREATED)

        except IntegrityError as e:
            if 'unique_answer' in str(e):
                return Response({'error': _('Correct answer already added.')},
                                status=status.HTTP_400_BAD_REQUEST)
            else:
                print(e)
                return Response({'error': _('Unhandled error occurred.')},
                                status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        except Exception as e:
            print(e)
            return Response({'error': _('Unhandled error occurred.')},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class InstituteTestAddUpdateAssertTrueFalseCorrectAnswer(APIView):
    """View for adding assert true false correct answer"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def post(self, request, *args, **kwargs):
        """Only subject in-charge can access."""
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug')
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectPermission.objects.filter(
                to=subject,
                invitee=self.request.user
        ).exists():
            return Response({'error': _('Permission denied [Subject in-charge only]')},
                            status=status.HTTP_400_BAD_REQUEST)

        question = models.SubjectTypedTestQuestion.objects.filter(
            pk=kwargs.get('question_id')
        ).only('pk').first()

        if not question:
            return Response({'error': _('Question not found.')},
                            status.HTTP_400_BAD_REQUEST)

        try:
            answer = models.SubjectTestAssertionCorrectAnswer.objects.filter(
                question=question
            ).first()

            if not answer:
                answer = models.SubjectTestAssertionCorrectAnswer.objects.create(
                    question=question,
                    correct_answer=request.data.get('correct_answer')
                )
            else:
                answer.correct_answer = request.data.get('correct_answer')
                answer.save()

            return Response({'correct_answer': answer.correct_answer},
                            status=status.HTTP_201_CREATED)

        except IntegrityError as e:
            if 'unique_answer' in str(e):
                return Response({'error': _('Correct answer already added.')},
                                status=status.HTTP_400_BAD_REQUEST)
            else:
                print(e)
                return Response({'error': _('Unhandled error occurred.')},
                                status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        except Exception as e:
            print(e)
            return Response({'error': _('Unhandled error occurred.')},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class InstituteTestAddUpdateFillInTheBlankChecking(APIView):
    """View for adding assert fill in the blank correct answer"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def post(self, request, *args, **kwargs):
        """Only subject in-charge can access."""
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug')
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectPermission.objects.filter(
                to=subject,
                invitee=self.request.user
        ).exists():
            return Response({'error': _('Permission denied [Subject in-charge only]')},
                            status=status.HTTP_400_BAD_REQUEST)

        question = models.SubjectTypedTestQuestion.objects.filter(
            pk=kwargs.get('question_id')
        ).only('pk').first()

        if not question:
            return Response({'error': _('Question not found.')},
                            status.HTTP_400_BAD_REQUEST)

        try:
            answer = models.SubjectTestFillInTheBlankCorrectAnswer.objects.filter(
                question=question
            ).first()
            status_response = status.HTTP_201_CREATED

            if not answer:
                answer = models.SubjectTestFillInTheBlankCorrectAnswer.objects.create(
                    question=question,
                    manual_checking=request.data.get('manual_checking'),
                    enable_strict_checking=request.data.get('enable_strict_checking'),
                    ignore_grammar=request.data.get('ignore_grammar'),
                    ignore_special_characters=request.data.get('ignore_special_characters'),
                    correct_answer=request.data.get('correct_answer'),
                )
            else:
                answer.correct_answer = request.data.get('correct_answer')
                answer.manual_checking = request.data.get('manual_checking')
                answer.enable_strict_checking = request.data.get('enable_strict_checking')
                answer.ignore_grammar = request.data.get('ignore_grammar')
                answer.ignore_special_characters = request.data.get('ignore_special_characters')
                answer.save()
                status_response = status.HTTP_200_OK

            return Response({
                'correct_answer': answer.correct_answer,
                'manual_checking': answer.manual_checking,
                'enable_strict_checking': answer.enable_strict_checking,
                'ignore_grammar': answer.ignore_grammar,
                'ignore_special_characters': answer.ignore_special_characters
            }, status=status_response)

        except IntegrityError as e:
                return Response({'error': _('Answer already added.')},
                                status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            print(e)
            return Response({'error': _('Unhandled error occurred.')},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class InstituteTestAddUpdateNumericCorrectAnswer(APIView):
    """View for adding numeric correct answer"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def post(self, request, *args, **kwargs):
        """Only subject in-charge can access."""
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug')
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectPermission.objects.filter(
                to=subject,
                invitee=self.request.user
        ).exists():
            return Response({'error': _('Permission denied [Subject in-charge only]')},
                            status=status.HTTP_400_BAD_REQUEST)

        question = models.SubjectTypedTestQuestion.objects.filter(
            pk=kwargs.get('question_id')
        ).only('pk').first()

        if not question:
            return Response({'error': _('Question not found.')},
                            status.HTTP_400_BAD_REQUEST)

        try:
            answer = models.SubjectTestNumericCorrectAnswer.objects.filter(
                question=question
            ).first()

            if not answer:
                answer = models.SubjectTestNumericCorrectAnswer.objects.create(
                    question=question,
                    correct_answer=request.data.get('correct_answer')
                )
            else:
                answer.correct_answer = request.data.get('correct_answer')
                answer.save()

            return Response({'correct_answer': answer.correct_answer},
                            status=status.HTTP_201_CREATED)

        except IntegrityError as e:
            if 'unique_numeric_answer' in str(e):
                return Response({'error': _('Correct answer already added.')},
                                status=status.HTTP_400_BAD_REQUEST)
            else:
                print(e)
                return Response({'error': _('Unhandled error occurred.')},
                                status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        except Exception as e:
            print(e)
            return Response({'error': _('Unhandled error occurred.')},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class InstituteTestAddUpdateMCQOption(APIView):
    """View for adding or updating mcq options"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def post(self, request, *args, **kwargs):
        """Only subject in-charge can access."""
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug')
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectPermission.objects.filter(
                to=subject,
                invitee=self.request.user
        ).exists():
            return Response({'error': _('Permission denied [Subject in-charge only]')},
                            status=status.HTTP_400_BAD_REQUEST)

        question = models.SubjectTypedTestQuestion.objects.filter(
            pk=kwargs.get('question_id')
        ).only('pk').first()

        if not question:
            return Response({'error': _('Question not found.')},
                            status.HTTP_400_BAD_REQUEST)

        try:
            if request.data.get('option_id'):
                option = models.SubjectTestMcqOptions.objects.filter(
                    pk=request.data.get('option_id')
                ).first()

                if not option:
                    return Response({'error': _('Mcq option not found!')},
                                    status=status.HTTP_400_BAD_REQUEST)

                option.option = request.data.get('option')
                option.correct_answer = request.data.get('correct_answer')
                option.save()
            else:
                option = models.SubjectTestMcqOptions.objects.create(
                    question=question,
                    option=request.data.get('option'),
                    correct_answer=request.data.get('correct_answer')
                )

            return Response({
                'option_id': option.pk,
                'option': option.option,
                'correct_answer': option.correct_answer
            }, status=status.HTTP_201_CREATED)

        except IntegrityError as e:
            if 'unique_mcq_correct_answer' in str(e):
                return Response({'error': _('Error! Can not have more than one correct choice.')},
                                status=status.HTTP_400_BAD_REQUEST)
            elif 'unique_mcq_option' in str(e):
                return Response({'error': _('Error! This choice was already added.')},
                                status=status.HTTP_400_BAD_REQUEST)
            else:
                print(e)
                return Response({'error': _('Unhandled error occurred.')},
                                status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        except Exception as e:
            print(e)
            return Response({'error': _('Unhandled error occurred.')},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class InstituteTestDeleteMCQOption(APIView):
    """View for deleting mcq options"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def delete(self, *args, **kwargs):
        """Only subject in-charge can access."""
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug')
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectPermission.objects.filter(
                to=subject,
                invitee=self.request.user
        ).exists():
            return Response({'error': _('Permission denied [Subject in-charge only]')},
                            status=status.HTTP_400_BAD_REQUEST)

        question = models.SubjectTypedTestQuestion.objects.filter(
            pk=kwargs.get('question_id')
        ).only('pk').first()

        if not question:
            return Response({'error': _('Question not found.')},
                            status.HTTP_400_BAD_REQUEST)

        option = models.SubjectTestMcqOptions.objects.filter(
            pk=kwargs.get('option_id'),
            question=kwargs.get('question_id')
        ).first()

        if not option:
            return Response({'error': _('Mcq option not found!')},
                            status=status.HTTP_400_BAD_REQUEST)

        option.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class InstituteTestAddUpdateSelectMultipleOption(APIView):
    """View for adding or updating select multiple options"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def post(self, request, *args, **kwargs):
        """Only subject in-charge can access."""
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug')
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectPermission.objects.filter(
                to=subject,
                invitee=self.request.user
        ).exists():
            return Response({'error': _('Permission denied [Subject in-charge only]')},
                            status=status.HTTP_400_BAD_REQUEST)

        question = models.SubjectTypedTestQuestion.objects.filter(
            pk=kwargs.get('question_id')
        ).only('pk').first()

        if not question:
            return Response({'error': _('Question not found.')},
                            status.HTTP_400_BAD_REQUEST)

        try:
            if request.data.get('option_id'):
                option = models.SubjectTestSelectMultipleCorrectAnswer.objects.filter(
                    pk=request.data.get('option_id')
                ).first()

                if not option:
                    return Response({'error': _('Option not found!')},
                                    status=status.HTTP_400_BAD_REQUEST)

                option.option = request.data.get('option')
                option.correct_answer = request.data.get('correct_answer')
                option.save()
            else:
                option = models.SubjectTestSelectMultipleCorrectAnswer.objects.create(
                    question=question,
                    option=request.data.get('option'),
                    correct_answer=request.data.get('correct_answer')
                )

            return Response({
                'option_id': option.pk,
                'option': option.option,
                'correct_answer': option.correct_answer
            }, status=status.HTTP_201_CREATED)

        except IntegrityError as e:
            if 'unique_option' in str(e):
                return Response({'error': _('Error! This option was already added.')},
                                status=status.HTTP_400_BAD_REQUEST)
            else:
                print(e)
                return Response({'error': _('Unhandled error occurred.')},
                                status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        except Exception as e:
            print(e)
            return Response({'error': _('Unhandled error occurred.')},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class InstituteTestDeleteMultipleChoiceOption(APIView):
    """View for deleting multiple choice options"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def delete(self, *args, **kwargs):
        """Only subject in-charge can access."""
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug')
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectPermission.objects.filter(
                to=subject,
                invitee=self.request.user
        ).exists():
            return Response({'error': _('Permission denied [Subject in-charge only]')},
                            status=status.HTTP_400_BAD_REQUEST)

        question = models.SubjectTypedTestQuestion.objects.filter(
            pk=kwargs.get('question_id')
        ).only('pk').first()

        if not question:
            return Response({'error': _('Question not found.')},
                            status.HTTP_400_BAD_REQUEST)

        option = models.SubjectTestSelectMultipleCorrectAnswer.objects.filter(
            pk=kwargs.get('option_id'),
            question=kwargs.get('question_id')
        ).first()

        if not option:
            return Response({'error': _('Option not found!')},
                            status=status.HTTP_400_BAD_REQUEST)

        option.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class InstituteTypedTestAddTestQuestionImage(APIView):
    """View for adding typed test question image"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def post(self, request, *args, **kwargs):
        """Only subject in-charge can access."""
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug')
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectPermission.objects.filter(
                to=subject,
                invitee=self.request.user
        ).exists():
            return Response({'error': _('Permission denied [Subject in-charge only]')},
                            status=status.HTTP_400_BAD_REQUEST)

        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug')
        ).only('institute_slug').first()

        if not institute:
            return Response({'error': _('Institute not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not get_active_common_license(institute):
            return Response({'error': _('Institute LMS CMS license expired or not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        institute_license_stats = models.InstituteLicenseStat.objects.filter(
            institute=institute
        ).only('total_storage').first()

        if not institute_license_stats.total_storage:
            return Response({'error': _('Storage license expired or not found. Purchase storage to upload files.')},
                            status=status.HTTP_400_BAD_REQUEST)

        image_error = validate_image_file(request.data.get('file'))

        if image_error:
            return image_error

        institute_stats = models.InstituteStatistics.objects.filter(
            institute=institute
        ).only('storage').first()

        if request.data.get('file').size / 1000000000 + \
                float(institute_stats.storage) > float(institute_license_stats.total_storage):
            return Response({'error': _('File size too large. Purchase additional storage to upload files.')},
                            status=status.HTTP_400_BAD_REQUEST)

        question = models.SubjectTypedTestQuestion.objects.filter(
            pk=kwargs.get('question_id')
        ).only('pk').first()

        if not question:
            return Response({'error': _('Question not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not question.has_picture:
            return Response({'error': _('Uploading picture to this question not allowed.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if models.SubjectTestQuestionImage.objects.filter(
            question=question
        ).exists():
            return Response({'error': _('Image already uploaded for this question.')},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            ser = serializer.SubjectTypedTestQuestionImageUploadSerializer(data={
                'file': request.data.get('file'),
                'question': question.pk
            }, context={'request': request})

            if ser.is_valid():
                ser.save()

                file_size = request.data.get('file').size / 1000000000  # Size in GB

                institute_stats.storage = Decimal(float(institute_stats.storage) + file_size)
                institute_stats.save()

                models.InstituteSubjectStatistics.objects.filter(
                    statistics_subject=subject
                ).update(storage=F('storage') + Decimal(file_size))

                return Response({'image': ser.data['file']}, status=status.HTTP_201_CREATED)
            else:
                return Response(ser.errors, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            print(e)
            return Response({'error': _('Unhandled error occurred.')},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class InstituteTypedTestDeleteTestQuestionImage(APIView):
    """View for deleting typed test question image"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def delete(self, *args, **kwargs):
        """Only subject in-charge can access."""
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug')
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectPermission.objects.filter(
                to=subject,
                invitee=self.request.user
        ).exists():
            return Response({'error': _('Permission denied [Subject in-charge only]')},
                            status=status.HTTP_400_BAD_REQUEST)

        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug')
        ).only('institute_slug').first()

        if not institute:
            return Response({'error': _('Institute not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        question_image = models.SubjectTestQuestionImage.objects.filter(
            question__pk=kwargs.get('question_id')
        ).first()

        if not question_image:
            return Response({'error': _('Image not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            file_size = question_image.file.size / 1000000000  # In GB
            question_image.delete()

            models.InstituteSubjectStatistics.objects.filter(
                statistics_subject=subject
            ).update(storage=F('storage') - Decimal(file_size))
            models.InstituteStatistics.objects.filter(
                institute=institute
            ).update(storage=F('storage') - Decimal(file_size))

            return Response(status=status.HTTP_204_NO_CONTENT)
        except Exception as e:
            print(e)
            return Response({'error': _('Unhandled error occurred.')},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class InstituteEditImageQuestionView(APIView):
    """View for editing image question paper"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def patch(self, request, *args, **kwargs):
        """Only subject in-charge can access."""
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug')
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug')
        ).only('institute_slug').first()

        if not institute:
            return Response({'error': _('Institute not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectPermission.objects.filter(
            to=subject,
            invitee=self.request.user
        ).exists():
            return Response({'error': _('Permission denied [Subject in-charge only]')},
                            status=status.HTTP_400_BAD_REQUEST)

        question_set = models.SubjectTestSets.objects.filter(
            pk=kwargs.get('set_id'),
            test__test_slug=kwargs.get('test_slug')
        ).first()

        if not question_set:
            return Response({'error': _('Question set not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if question_set.mark_as_final:
            return Response({'error': _('Question set is MARKED AS FINAL. Editing question is not allowed.')},
                            status.HTTP_400_BAD_REQUEST)

        question = models.SubjectPictureTestQuestion.objects.filter(
            pk=kwargs.get('question_id'),
            test__test_slug=kwargs.get('test_slug')
        ).first()

        if not question:
            return Response({'error': _('Question not found. Please refresh and try again.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if request.data.get('concept_label'):
            concept_label = models.SubjectTestConceptLabels.objects.filter(
                pk=request.data.get('concept_label')
            ).first()

            if concept_label:
                question.concept_label = concept_label
            else:
                return Response({'error': _('Concept label not found. Please refresh and try again.')},
                                status=status.HTTP_400_BAD_REQUEST)
        else:
            question.concept_label = None

        try:
            if request.data.get('text'):
                question.text = request.data.get('text')
            else:
                question.text = ''

            question.marks = request.data.get('marks')
            question.save()

            response = {
                'question_id': question.pk,
                'file': self.request.build_absolute_uri('/').strip('/') + MEDIA_URL + '/' + str(question.file),
                'text': question.text,
                'marks': question.marks,
                'order': question.order,
            }
            if question.concept_label:
                response['concept_label_id'] = question.concept_label.pk

            return Response(response, status=status.HTTP_200_OK)

        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            print(e)
            return Response({'error': _('Unhandled error occurred.')},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class InstituteDeleteImageQuestionView(APIView):
    """View for deleting image question paper"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def delete(self, *args, **kwargs):
        """Only subject in-charge or admin can access."""
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug')
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug')
        ).only('institute_slug').first()

        if not institute:
            return Response({'error': _('Institute not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectPermission.objects.filter(
            to=subject,
            invitee=self.request.user
        ).exists():
            if not models.InstitutePermission.objects.filter(
                institute=institute,
                invitee=self.request.user,
                role=models.InstituteRole.ADMIN,
                active=True
            ):
                return Response({'error': _('Permission denied [Subject in-charge or Admin only]')},
                                status=status.HTTP_400_BAD_REQUEST)

        question_set = models.SubjectTestSets.objects.filter(
            pk=kwargs.get('set_id'),
            test__test_slug=kwargs.get('test_slug')
        ).first()

        if not question_set:
            return Response({'error': _('Question set not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if question_set.mark_as_final:
            return Response({'error': _('Question set is MARKED AS FINAL. Uploading question is not allowed.')},
                            status.HTTP_400_BAD_REQUEST)

        question = models.SubjectPictureTestQuestion.objects.filter(
            pk=kwargs.get('question_id'),
            test__test_slug=kwargs.get('test_slug')
        ).first()

        if not question:
            return Response({'error': _('Question not found. Please refresh and try again.')},
                            status=status.HTTP_400_BAD_REQUEST)

        file_size = question.file.size / 1000000000

        question.delete()
        models.InstituteSubjectStatistics.objects.filter(
            statistics_subject=subject
        ).update(storage=F('storage') - Decimal(file_size))
        models.InstituteStatistics.objects.filter(
            institute=institute
        ).update(storage=F('storage') - Decimal(file_size))

        question_set.verified = False
        question_set.active = False
        question_set.mark_as_final = False
        question_set.save()

        return Response(status=status.HTTP_204_NO_CONTENT)


class InstituteDeleteFileQuestionPaperView(APIView):
    """View for deleting file question paper"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def delete(self, *args, **kwargs):
        """Only subject in-charge or admin can access."""
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug')
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug')
        ).only('institute_slug').first()

        if not institute:
            return Response({'error': _('Institute not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectPermission.objects.filter(
            to=subject,
            invitee=self.request.user
        ).exists():
            if not models.InstitutePermission.objects.filter(
                institute=institute,
                invitee=self.request.user,
                role=models.InstituteRole.ADMIN,
                active=True
            ):
                return Response({'error': _('Permission denied [Subject in-charge or Admin only]')},
                                status=status.HTTP_400_BAD_REQUEST)

        question_set = models.SubjectTestSets.objects.filter(
            pk=kwargs.get('set_id'),
            test__test_slug=kwargs.get('test_slug')
        ).first()

        if not question_set:
            return Response({'error': _('Question set not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        question_paper = models.SubjectFileTestQuestion.objects.filter(
            set=question_set
        ).first()

        if not question_paper:
            return Response({'error': _('Question paper not found. Please refresh and try again.')},
                            status=status.HTTP_400_BAD_REQUEST)

        file_size = question_paper.file.size / 1000000000

        question_paper.delete()
        models.InstituteSubjectStatistics.objects.filter(
            statistics_subject=subject
        ).update(storage=F('storage') - Decimal(file_size))
        models.InstituteStatistics.objects.filter(
            institute=institute
        ).update(storage=F('storage') - Decimal(file_size))

        question_set.verified = False
        question_set.active = False
        question_set.mark_as_final = False
        question_set.save()

        return Response(status=status.HTTP_204_NO_CONTENT)


class InstituteAddTestConceptLabelView(APIView):
    """View for adding test concept label"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def post(self, request, *args, **kwargs):
        """Only subject in-charge can access."""
        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug')
        ).only('institute_slug').first()

        if not institute:
            return Response({'error': _('Institute not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug')
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectPermission.objects.filter(
            to=subject,
            invitee=self.request.user
        ).exists():
            return Response({'error': _('Permission denied [Subject in-charge only]')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not get_active_common_license(institute):
            return Response({'error': _('Active LMS CMS license not found or expired')},
                            status=status.HTTP_400_BAD_REQUEST)

        test = models.SubjectTest.objects.filter(
            test_slug=kwargs.get('test_slug'),
            subject=subject
        ).only('question_mode').first()

        if not test:
            return Response({'error': _('Test not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            label = models.SubjectTestConceptLabels.objects.create(
                test=test,
                name=request.data.get('name')
            )
            return Response({'id': label.pk, 'name': label.name},
                            status=status.HTTP_201_CREATED)
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)


class InstituteDeleteTestConceptLabelView(APIView):
    """View for deleting test concept label"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def delete(self, *args, **kwargs):
        """Only subject in-charge, admin can access."""
        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug')
        ).only('institute_slug').first()

        if not institute:
            return Response({'error': _('Institute not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug')
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectPermission.objects.filter(
            to=subject,
            invitee=self.request.user
        ).exists():
            if not models.InstitutePermission.objects.filter(
                institute=institute,
                invitee=self.request.user,
                role=models.InstituteRole.ADMIN,
                active=True
            ).exists():
                return Response({'error': _('Permission denied [Subject in-charge or Admin only]')},
                                status=status.HTTP_400_BAD_REQUEST)

        try:
            label = models.SubjectTestConceptLabels.objects.filter(
                pk=kwargs.get('label_id'),
                test__subject=subject
            ).first()

            if not label:
                return Response({'error': _('Concept label not found.')},
                                status=status.HTTP_400_BAD_REQUEST)

            label.delete()

            return Response(status.HTTP_204_NO_CONTENT)
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)


class InstituteAddTestQuestionSectionView(APIView):
    """View for adding test question section"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def post(self, request, *args, **kwargs):
        """Only subject in-charge can access."""
        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug')
        ).only('institute_slug').first()

        if not institute:
            return Response({'error': _('Institute not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug')
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectPermission.objects.filter(
            to=subject,
            invitee=self.request.user
        ).exists():
            return Response({'error': _('Permission denied [Subject in-charge only]')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not get_active_common_license(institute):
            return Response({'error': _('Active LMS CMS license not found or expired.')},
                            status=status.HTTP_400_BAD_REQUEST)

        test = models.SubjectTest.objects.filter(
            test_slug=kwargs.get('test_slug'),
            subject=subject
        ).only('id').first()

        if not test:
            return Response({'error': _('Test not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        question_set = models.SubjectTestSets.objects.filter(
            pk=kwargs.get('set_id'),
            test=test
        ).only('pk').first()

        if not question_set:
            return Response({'error': _('Question set not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            question_section = models.SubjectTestQuestionSection.objects.create(
                test=test,
                set=question_set,
                section_mandatory=request.data.get('section_mandatory'),
                view=request.data.get('view'),
                no_of_question_to_attempt=request.data.get('no_of_question_to_attempt'),
                answer_all_questions=request.data.get('answer_all_questions'),
                name=request.data.get('name'),
            )
            return Response({
                'section_id': question_section.id,
                'section_mandatory': question_section.section_mandatory,
                'view': question_section.view,
                'no_of_question_to_attempt': question_section.no_of_question_to_attempt,
                'answer_all_questions': question_section.answer_all_questions,
                'name': question_section.name,
                'questions': list()
            }, status=status.HTTP_201_CREATED)
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            print(e)
            return Response({'error': _('Server Error! Unhandled error occurred.')},
                            status=status.HTTP_400_BAD_REQUEST)


class InstituteEditTestQuestionSectionView(APIView):
    """View for editing test question section"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def patch(self, request, *args, **kwargs):
        """Only subject in-charge can access."""
        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug')
        ).only('institute_slug').first()

        if not institute:
            return Response({'error': _('Institute not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug')
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectPermission.objects.filter(
            to=subject,
            invitee=self.request.user
        ).exists():
            return Response({'error': _('Permission denied [Subject in-charge only]')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not get_active_common_license(institute):
            return Response({'error': _('Active LMS CMS license not found or expired.')},
                            status=status.HTTP_400_BAD_REQUEST)

        test = models.SubjectTest.objects.filter(
            test_slug=kwargs.get('test_slug'),
            subject=subject
        ).only('id').first()

        if not test:
            return Response({'error': _('Test not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        question_section = models.SubjectTestQuestionSection.objects.filter(
            pk=kwargs.get('question_section_id'),
            test=test
        ).first()

        if not question_section:
            return Response({'error': _('Question group not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            if request.data.get('no_of_question_to_attempt'):
                question_section.no_of_question_to_attempt = request.data.get('no_of_question_to_attempt')

            question_section.answer_all_questions = request.data.get('answer_all_questions')
            question_section.section_mandatory = request.data.get('section_mandatory')
            question_section.view = request.data.get('view')
            question_section.name = request.data.get('name')
            question_section.save()

            return Response({
                'section_id': question_section.id,
                'section_mandatory': question_section.section_mandatory,
                'view': question_section.view,
                'no_of_question_to_attempt': question_section.no_of_question_to_attempt,
                'answer_all_questions': question_section.answer_all_questions,
                'name': question_section.name
            }, status=status.HTTP_200_OK)
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            print(e)
            return Response({'error': _('Server Error! Unhandled error occurred.')},
                            status=status.HTTP_400_BAD_REQUEST)


class InstituteEditQuestionSetName(APIView):
    """View for editing test question set name"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def patch(self, request, *args, **kwargs):
        """Only subject in-charge can access."""
        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug')
        ).only('institute_slug').first()

        if not institute:
            return Response({'error': _('Institute not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug')
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectPermission.objects.filter(
            to=subject,
            invitee=self.request.user
        ).exists():
            return Response({'error': _('Permission denied [Subject in-charge only]')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not get_active_common_license(institute):
            return Response({'error': _('Active LMS CMS license not found or expired.')},
                            status=status.HTTP_400_BAD_REQUEST)

        test = models.SubjectTest.objects.filter(
            test_slug=kwargs.get('test_slug'),
            subject=subject
        ).only('id').first()

        if not test:
            return Response({'error': _('Test not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        question_set = models.SubjectTestSets.objects.filter(
            pk=kwargs.get('set_id'),
            test=test
        ).only('pk', 'set_name').first()

        if not question_set:
            return Response({'error': _('Question set not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            question_set.set_name = request.data.get('set_name')
            question_set.save()

            return Response({
                'id': question_set.pk,
                'set_name': question_set.set_name
            }, status=status.HTTP_200_OK)
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            print(e)
            return Response({'error': _('Server Error! Unhandled error occurred.')},
                            status=status.HTTP_400_BAD_REQUEST)


class InstituteRemoveConceptLabelFromQuestion(APIView):
    """View for removing concept label from question"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def patch(self, request, *args, **kwargs):
        """Only subject in-charge can access."""
        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug')
        ).only('institute_slug').first()

        if not institute:
            return Response({'error': _('Institute not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug')
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectPermission.objects.filter(
            to=subject,
            invitee=self.request.user
        ).exists():
            return Response({'error': _('Permission denied [Subject in-charge only]')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not get_active_common_license(institute):
            return Response({'error': _('Active LMS CMS license not found or expired.')},
                            status=status.HTTP_400_BAD_REQUEST)

        test = models.SubjectTest.objects.filter(
            test_slug=kwargs.get('test_slug'),
            subject=subject
        ).only('question_mode').first()

        if not test:
            return Response({'error': _('Test not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        question = None

        if test.question_mode == models.QuestionMode.IMAGE:
            question = models.SubjectPictureTestQuestion.objects.filter(
                pk=kwargs.get('question_id'),
                test=test
            ).only('concept_label').first()
        elif test.question_mode == models.QuestionMode.TYPED:
            # Find the question of typed question
            pass

        if not question:
            return Response({'error': _('Question not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            question.concept_label = None
            question.save()

            return Response(status=status.HTTP_200_OK)
        except Exception as e:
            print(e)
            return Response({'error': _('Server Error! Unhandled error occurred.')},
                            status=status.HTTP_400_BAD_REQUEST)


class InstituteDeleteQuestionSection(APIView):
    """View for deleting question section"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def delete(self, *args, **kwargs):
        """Only subject in-charge can access."""
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug')
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug')
        ).only('institute_slug').first()

        if not institute:
            return Response({'error': _('Institute not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectPermission.objects.filter(
            to=subject,
            invitee=self.request.user
        ).exists():
            return Response({'error': _('Permission denied [Subject in-charge only]')},
                            status=status.HTTP_400_BAD_REQUEST)

        question_paper_section = models.SubjectTestQuestionSection.objects.filter(
            pk=kwargs.get('question_section_id'),
            test__test_slug=kwargs.get('test_slug')
        ).first()

        if not question_paper_section:
            return Response({'error': _('Question group not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        file_size = 0.0

        if question_paper_section.test.question_mode == models.QuestionMode.IMAGE:
            # Find size of image questions in this section
            for q in models.SubjectPictureTestQuestion.objects.filter(
                test_section=question_paper_section
            ).only('file'):
                file_size += q.file.size

        elif question_paper_section.test.question_mode == models.QuestionMode.TYPED:
            # Find size of typed questions in this section
            for q in models.SubjectTypedTestQuestion.objects.filter(
                test_section=question_paper_section,
                has_picture=True
            ):
                pic = models.SubjectTestQuestionImage.objects.filter(
                    question__pk=q.pk
                ).first()

                if pic:
                    file_size += pic.file.size

        try:
            question_paper_section.delete()

            if file_size > 0.0:
                file_size = file_size / 1000000000

                models.InstituteSubjectStatistics.objects.filter(
                    statistics_subject=subject
                ).update(storage=F('storage') - Decimal(file_size))
                models.InstituteStatistics.objects.filter(
                    institute=institute
                ).update(storage=F('storage') - Decimal(file_size))

            return Response(status=status.HTTP_204_NO_CONTENT)
        except Exception:
            return Response({'error': _('Unhandled exception occurred.')},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class InstituteDeleteQuestionSet(APIView):
    """View for deleting question paper"""
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated, IsTeacher)

    def delete(self, *args, **kwargs):
        """Only subject in-charge or admin can access."""
        subject = models.InstituteSubject.objects.filter(
            subject_slug=kwargs.get('subject_slug')
        ).only('subject_slug').first()

        if not subject:
            return Response({'error': _('Subject not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        institute = models.Institute.objects.filter(
            institute_slug=kwargs.get('institute_slug')
        ).only('institute_slug').first()

        if not institute:
            return Response({'error': _('Institute not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        if not models.InstituteSubjectPermission.objects.filter(
            to=subject,
            invitee=self.request.user
        ).exists():
            if not models.InstitutePermission.objects.filter(
                institute=institute,
                invitee=self.request.user,
                role=models.InstituteRole.ADMIN,
                active=True
            ):
                return Response({'error': _('Permission denied [Subject in-charge or Admin only]')},
                                status=status.HTTP_400_BAD_REQUEST)

        question_set = models.SubjectTestSets.objects.filter(
            pk=kwargs.get('set_id'),
            test__test_slug=kwargs.get('test_slug')
        ).first()

        if not question_set:
            return Response({'error': _('Question set not found.')},
                            status=status.HTTP_400_BAD_REQUEST)

        file_size = 0.0

        if question_set.test.question_mode == models.QuestionMode.FILE:
            # Find size of file question paper
            question_paper = models.SubjectFileTestQuestion.objects.filter(
                set=question_set,
                test__test_slug=kwargs.get('test_slug')
            ).only('file').first()

            if question_paper:
                file_size += question_paper.file.size

            # Find size due to student responses (if any)
        elif question_set.test.question_mode == models.QuestionMode.IMAGE:
            # Find size of image questions
            for q in models.SubjectPictureTestQuestion.objects.filter(
                test_section__set__pk=question_set.pk
            ).only('file'):
                file_size += q.file.size
        elif question_set.test.question_mode == models.QuestionMode.TYPED:
            for q in models.SubjectTypedTestQuestion.objects.filter(
                test_section__set__pk=question_set.pk,
                has_picture=True
            ):
                pic = models.SubjectTestQuestionImage.objects.filter(
                    question__pk=q.pk
                ).first()

                if pic:
                    file_size += pic.file.size

        question_set.delete()
        file_size = file_size / 1000000000

        if file_size > 0.0:
            models.InstituteSubjectStatistics.objects.filter(
                statistics_subject=subject
            ).update(storage=F('storage') - Decimal(file_size))
            models.InstituteStatistics.objects.filter(
                institute=institute
            ).update(storage=F('storage') - Decimal(file_size))

        return Response(status=status.HTTP_204_NO_CONTENT)
