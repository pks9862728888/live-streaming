import { authTokenName } from './../../constants';
import { InAppDataTransferService } from '../in-app-data-transfer.service';
import { Component, OnInit, OnDestroy } from '@angular/core';
import { CookieService } from 'ngx-cookie-service';
import { Router } from '@angular/router';
import { MediaMatcher } from '@angular/cdk/layout';
import { Subscription } from 'rxjs';

@Component({
  selector: 'app-coaching-workspace',
  templateUrl: './coaching-workspace.component.html',
  styleUrls: ['./coaching-workspace.component.css']
})
export class CoachingWorkspaceComponent implements OnInit, OnDestroy {

  // For showing sidenav toolbar
  mobileQuery: MediaQueryList;
  opened: boolean;
  activeLink: string;
  navbarActiveLinkSubscription: Subscription;
  showTempNamesSubscription: Subscription;
  tempBreadcrumbLinkName: string;

  constructor( private cookieService: CookieService,
               private router: Router,
               private media: MediaMatcher,
               private inAppDataTransferService: InAppDataTransferService ) {

    this.mobileQuery = this.media.matchMedia('(max-width: 600px)');

    // Initializing sidenav active route in case page is reloaded
    const active_route = localStorage.getItem('activeRoute');
    if (active_route) {
      this.activeLink = active_route;
    } else {
      localStorage.setItem('activeRoute', 'INSTITUTES');
      this.router.navigate(['teacher-workspace/institutes']);
    }

    // If auth token is not saved then redirecting to login page
    if (!this.cookieService.get(authTokenName)) {
      this.router.navigate(['/login']);
    }
  }

  ngOnInit(): void {
    // For keeping the sidenav opened in desktop view in the beginning
    if (this.mobileQuery.matches === true) {
      this.opened = false;
    } else {
      this.opened = true;
    }

    this.showTempNamesSubscription = this.inAppDataTransferService.activeBreadcrumbLinkData$.subscribe(
      (linkName: string) => {
        this.tempBreadcrumbLinkName = linkName;
      }
    );
  }

  // For navigating to teacher-workspace view
  navigate(link: string) {
    if (link !== this.activeLink) {
      this.activeLink = link;

      if (this.activeLink === 'HOME') {
        this.router.navigate(['/home']);
      } else if (this.activeLink === 'EXIT') {
        localStorage.setItem('activeRoute', 'INSTITUTES');
        localStorage.removeItem('currentInstituteSlug');
        localStorage.removeItem('currentInstituteRole');
        this.router.navigate(['/teacher-workspace/institutes']);
      } else {
        const instituteSlug = localStorage.getItem('currentInstituteSlug');
        if (this.activeLink === 'COACHING_PROFILE') {
          this.router.navigate(['/coaching-workspace/' + instituteSlug + '/profile']);
        } else if (this.activeLink === 'COACHING_PERMISSIONS') {
          this.router.navigate(['/coaching-workspace/' + instituteSlug + '/permissions']);
        } else if (this.activeLink === 'COACHING_CLASSES') {
          this.router.navigate(['/coaching-workspace/' + instituteSlug + '/classes']);
        } else if (this.activeLink === 'LICENSE') {
          this.router.navigate(['/coaching-workspace/' + instituteSlug + '/license']);
        }
      }
    }
  }

  // For navbar
  performAction(link: string) {
    // Hiding navbar if it is mobile
    if (this.mobileQuery.matches === true) {
      this.opened = false;
    }
    this.navigate(link);
  }

  tempBreadCrumbNameExists() {
    if (this.tempBreadcrumbLinkName) {
      return true;
    } else {
      return false;
    }
  }

  ngOnDestroy(): void {}

}
