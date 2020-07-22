import { InAppDataTransferService } from '../in-app-data-transfer.service';
import { Component, OnInit, OnDestroy } from '@angular/core';
import { Router } from '@angular/router';
import { MediaMatcher } from '@angular/cdk/layout';
import { Subscription } from 'rxjs';

@Component({
  selector: 'app-school-workspace',
  templateUrl: './school-workspace.component.html',
  styleUrls: ['./school-workspace.component.css']
})
export class SchoolWorkspaceComponent implements OnInit, OnDestroy {

  // For showing sidenav toolbar
  mobileQuery: MediaQueryList;
  opened: boolean;
  activeLink: string;
  navbarActiveLinkSubscription: Subscription;
  showTempNamesSubscription: Subscription;
  tempBreadcrumbLinkName: string;

  constructor( private router: Router,
               private media: MediaMatcher,
               private inAppDataTransferService: InAppDataTransferService ) {

    this.mobileQuery = this.media.matchMedia('(max-width: 768px)');

    // Initializing sidenav active route in case page is reloaded
    const active_route = sessionStorage.getItem('activeRoute');
    if (active_route) {
      this.activeLink = active_route;
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
        sessionStorage.setItem('activeRoute', 'INSTITUTES');
        sessionStorage.removeItem('currentInstituteSlug');
        sessionStorage.removeItem('currentInstituteRole');
        this.router.navigate(['/teacher-workspace/institutes']);
      } else {
        const instituteSlug = sessionStorage.getItem('currentInstituteSlug');
        if (this.activeLink === 'SCHOOL_PROFILE') {
          this.router.navigate(['/school-workspace/' + instituteSlug + '/profile']);
        } else if (this.activeLink === 'SCHOOL_PERMISSIONS') {
          this.router.navigate(['/school-workspace/' + instituteSlug + '/permissions']);
        } else if (this.activeLink === 'SCHOOL_CLASSES') {
          this.router.navigate(['/school-workspace/' + instituteSlug + '/classes']);
        } else if (this.activeLink === 'LICENSE') {
          this.router.navigate(['/school-workspace/' + instituteSlug + '/license']);
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
