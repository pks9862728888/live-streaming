import { HttpClient, HttpHeaders } from '@angular/common/http';
import { baseUrl } from '../../urls';
import { CookieService } from 'ngx-cookie-service';
import { Injectable } from '@angular/core';
import { authTokenName } from './../../constants';
import { PaymentSuccessCallbackResponse } from './../license/license.model';

interface FormDataInterface {
  name: string;
  country: string;
  institute_category: string;
  institute_profile: {
    motto: string;
    email: string;
    phone: string;
    website_url: string;
    state: string;
    pin: string;
    address: string;
    recognition: string;
    primary_language: string;
    secondary_language: string;
    tertiary_language: string;
  };
}

interface InviterUserInterface {
  role: string;
  invitee: string;
}


@Injectable({
  providedIn: 'root'
})
export class InstituteApiService {

  // Urls for communicating with backend
  baseUrl = baseUrl;
  instituteBaseUrl = `${baseUrl}institute/`;
  instituteMinDetailsAdminUrl = `${this.instituteBaseUrl}institute-min-details-teacher-admin`;
  instituteJoinedDetailUrl = `${this.instituteBaseUrl}joined-institutes-teacher`;
  institutePendingInvitesUrl = `${this.instituteBaseUrl}pending-institute-invites-teacher`;
  instituteCreateUrl = `${this.instituteBaseUrl}create`;
  instituteLicenseListUrl = `${this.instituteBaseUrl}institute-license-list`;
  instituteSelectedLicenseDetail = `${this.instituteBaseUrl}institute-license-detail`;
  instituteDiscountCouponDetailUrl = `${this.instituteBaseUrl}get-discount-coupon`;
  licenseSelectPlanUrl = `${this.instituteBaseUrl}select-license`;
  createLicensePurchaseOrderUrl = `${this.instituteBaseUrl}create-order`;
  razorpayCallbackUrl = `${this.instituteBaseUrl}razorpay-payment-callback`;

  getInstituteDetailUrl(instituteSlug: string) {
    return `${this.instituteBaseUrl}detail/${instituteSlug}`;
  }

  getUserListUrl(instituteSlug: string, role: string) {
    return `${this.instituteBaseUrl}${instituteSlug}/${role}/get-user-list`;
  }

  getUserInviteUrl(instituteSlug: string) {
    return `${this.instituteBaseUrl}${instituteSlug}/provide-permission`;
  }

  getInstituteJoinDeclineUrl(instituteSlug: string) {
    return `${this.instituteBaseUrl}${instituteSlug}/accept-delete-permission`;
  }

  getInstituteLicensePurchasedUrl(instituteSlug: string) {
    return `${this.instituteBaseUrl}${instituteSlug}/get-license-purchased`;
  }


  constructor( private cookieService: CookieService,
               private httpClient: HttpClient ) { }

  // Get minimum details of institute for admin teacher of institute
  getTeacherAdminInstituteMinDetails() {
    return this.httpClient.get(this.instituteMinDetailsAdminUrl, {headers: this.getAuthHeader()});
  }

  getJoinedInstituteMinDetails() {
    return this.httpClient.get(this.instituteJoinedDetailUrl, {headers: this.getAuthHeader()});
  }

  getInvitedInstituteMinDetails() {
    return this.httpClient.get(this.institutePendingInvitesUrl, {headers: this.getAuthHeader()});
  }

  // Create an institute
  createInstitute(fromData: FormDataInterface) {
    return this.httpClient.post(this.instituteCreateUrl, JSON.stringify(fromData), {headers: this.getAuthHeader()});
  }

  // Get institute details
  getInstituteDetails(instituteSlug: string) {
    return this.httpClient.get(this.getInstituteDetailUrl(instituteSlug), {headers: this.getAuthHeader()});
  }

  // Get list of admins
  getUserList(instituteSlug: string, role:string) {
    return this.httpClient.get(
      this.getUserListUrl(instituteSlug, role),
      { headers: this.getAuthHeader() });
  }

  // Invite new user
  inviteUser(instituteSlug: string, payload: InviterUserInterface) {
    return this.httpClient.post(
      this.getUserInviteUrl(instituteSlug), payload,
      { headers: this.getAuthHeader() }
    );
  }

  // Decline invitation
  acceptDeleteInstituteJoinInvitation(instituteSlug: string, operation: string) {
    return this.httpClient.post(
      this.getInstituteJoinDeclineUrl(instituteSlug),
      { 'operation': operation.toUpperCase()},
      { headers: this.getAuthHeader() }
    );
  }

  // Get institute license list
  getInstituteLicenseList() {
    return this.httpClient.get(
      this.instituteLicenseListUrl,
      { 'headers': this.getAuthHeader() });
  }

  // Get specific license details
  getSelectedLicenseDetails(id: string) {
    return this.httpClient.post(
      this.instituteSelectedLicenseDetail,
      {'id': id},
      { headers: this.getAuthHeader() }
    );
  }

  // Get coupon details
  getDiscountCouponDetails(couponCode: string) {
    return this.httpClient.post(
      this.instituteDiscountCouponDetailUrl,
      { 'coupon_code': couponCode },
      { headers: this.getAuthHeader() }
    );
  }

  // To initiate purchase request
  purchase(institute_slug: string, license_id: string, coupon_code: string) {
    return this.httpClient.post(
      this.licenseSelectPlanUrl,
      { 'institute_slug': institute_slug, 'license_id': license_id, 'coupon_code': coupon_code },
      { headers: this.getAuthHeader() }
    );
  }

  // To create order for license purchase
  createOrder(instituteSlug: string, selectedLicensePlanId: string, paymentGateway: string) {
    return this.httpClient.post(
      this.createLicensePurchaseOrderUrl,
      {
        'institute_slug': instituteSlug,
        'payment_gateway': paymentGateway,
        'license_id': selectedLicensePlanId
      },
      { headers: this.getAuthHeader() }
    );
  }

  // To send razorpay callback to server
  sendCallbackAndVerifyPayment(data: PaymentSuccessCallbackResponse, order_details_id: string) {
    return this.httpClient.post(
      this.razorpayCallbackUrl,
      {
        'razorpay_order_id': data.razorpay_order_id,
        'razorpay_payment_id': data.razorpay_payment_id,
        'razorpay_signature': data.razorpay_signature,
        'order_details_id': order_details_id
      },
      { headers: this.getAuthHeader() }
    );
  }

  // To get license purchase details of institue
  getInstituteLicensePurchased(instituteSlug: string) {
    return this.httpClient.get(
      this.getInstituteLicensePurchasedUrl(instituteSlug),
      { headers: this.getAuthHeader() }
    );
  }

  // To load token from storage
  loadToken() {
    return this.cookieService.get(authTokenName);
  }

  getAuthHeader() {
    return new HttpHeaders({
      'Content-Type': 'application/json',
      Authorization: `Token ${this.loadToken()}`
    });
  }

  getAuthTokenHeader() {
    return new HttpHeaders({
      Authorization: `Token ${this.loadToken()}`
    });
  }
}
