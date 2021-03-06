import { Injectable } from '@angular/core';
import { HttpHeaders, HttpClient } from '@angular/common/http';
import { CookieService } from 'ngx-cookie-service';
import { Subject } from 'rxjs';
import { baseUrl } from '../../urls';

interface LoginFormFormat {
  email: string;
  password: string;
}

interface SignupFormFormat {
  email: string;
  username: string;
  password: string;
  userIsStudent: boolean;
}

@Injectable({
  providedIn: 'root'
})
export class AuthService {

  // Urls for communicating with backend
  baseUrl = baseUrl;
  signupUrl = `${baseUrl}user/signup`;
  loginUrl = `${baseUrl}user/login`;

  // Headers for sending form data
  headers = new HttpHeaders({
    'Content-Type': 'application/json'
  });

  // Creating observable to send response in case of user login
  private userLoggedInSignalSource = new Subject<boolean>();
  userLoggedInSignalSource$ = this.userLoggedInSignalSource.asObservable();

  constructor( private cookieService: CookieService,
               private httpClient: HttpClient ) {}

  // Method to signup a new user
  signup(formData: SignupFormFormat) {
    const body = {
      email: formData.email,
      username: formData.username,
      password: formData.password,
      is_teacher: JSON.stringify(formData.userIsStudent) === JSON.stringify('false') ? true : false,
      is_student: JSON.stringify(formData.userIsStudent) === JSON.stringify('true') ? true : false
    };
    return this.httpClient.post(this.signupUrl, JSON.stringify(body), {headers: this.headers});
  }

  login(formData: LoginFormFormat) {
    return this.httpClient.post(this.loginUrl, JSON.stringify(formData), {headers: this.headers});
  }

  logout() {
    this.cookieService.deleteAll('/');
    sessionStorage.clear();
    localStorage.clear();
    this.sendLoggedInStatusSignal(false);
  }

  // This method sends login status signal as true
  sendLoggedInStatusSignal(status: boolean) {
    this.userLoggedInSignalSource.next(status);
  }

}
