import { hasSectionPerm, hasClassPerm, userId, currentClassSlug, currentInstituteRole, INSTITUTE_ROLE_REVERSE } from './../../../constants';
import { currentSectionSlug } from '../../../constants';
import { SectionDetailsResponse, SectionInchargeDetails } from './../../models/section.model';
import { Component, OnInit } from '@angular/core';
import { Subscription, Subject } from 'rxjs';
import { MediaMatcher } from '@angular/cdk/layout';
import { InstituteApiService } from 'src/app/services/institute-api.service';
import { UiService } from 'src/app/services/ui.service';
import { Router } from '@angular/router';


@Component({
  selector: 'app-class-section',
  templateUrl: './class-section.component.html',
  styleUrls: ['./class-section.component.css']
})
export class ClassSectionComponent implements OnInit {

  mq: MediaQueryList;
  showLoadingIndicator: boolean;
  showReloadError: boolean;
  showReloadText = 'Unable to fetch section list...';
  loadingText = 'Fetching Section List...';
  currentClassSlug: string;
  sectionStep: number;
  sectionList: SectionDetailsResponse[];
  errorText: string;
  successText: string;
  showCreateSectionFormMb: boolean;
  createSectionIndicator: boolean;
  subscribedDialogData: Subscription;
  inputPlaceholder = 'Section Name';
  createButtonText = 'Create';
  createProgressSpinnerText = 'Creating section...';
  hasClassPerm: boolean;
  maxSectionNameLength = 20;
  formEvent = new Subject<string>();

  constructor(
    private media: MediaMatcher,
    private instituteApiService: InstituteApiService,
    private router: Router,
    private uiService: UiService
    ) {
    this.mq = this.media.matchMedia('(max-width: 768px)');
    this.currentClassSlug = sessionStorage.getItem(currentClassSlug);
    if (sessionStorage.getItem(hasClassPerm) === 'true') {
      this.hasClassPerm = true;
    } else {
      this.hasClassPerm = false;
    }
  }

  ngOnInit(): void {
    this.getSectionList();
  }

  getSectionList() {
    this.showLoadingIndicator = true;
    this.showReloadError = false;
    this.errorText = null;
    this.instituteApiService.getInstituteSectionList(this.currentClassSlug).subscribe(
      (result: SectionDetailsResponse[]) => {
        this.showLoadingIndicator = false;
        this.sectionList = result;
      },
      errors => {
        this.showLoadingIndicator = false;
        if (errors.error) {
          if (errors.error.error) {
            this.errorText = errors.error.error;
          } else {
            this.showReloadError = true;
          }
        } else {
          this.showReloadError = true;
        }
      }
    )
  }

  createSection(sectionName: string) {
    this.createSectionIndicator = true;
    this.errorText = null;
    this.successText = null;
    this.formEvent.next('disable');
    this.instituteApiService.createClassSection(this.currentClassSlug, sectionName).subscribe(
      (result: SectionDetailsResponse) => {
        this.createSectionIndicator = false;
        this.successText = 'Section created successfully!';
        this.formEvent.next('reset');
        this.showCreateSectionFormMb = false;
        this.sectionList.push(result);
      },
      errors => {
        this.createSectionIndicator = false;
        this.formEvent.next('enable');
        if (errors.error) {
          if (errors.error.error) {
            this.errorText = errors.error.error;
          } else {
            this.errorText = 'Section with same name exists.';
          }
        } else {
          this.errorText = 'Section creation failed.';
        }
      }
    )
  }

  openSection(sectionSlug: string, hasSectionPerm_: boolean) {
    sessionStorage.setItem(currentSectionSlug, sectionSlug);
    if (hasSectionPerm_) {
      sessionStorage.setItem(hasSectionPerm, 'true');
    } else {
      sessionStorage.setItem(hasSectionPerm, 'false');
    }
    this.router.navigate(['/section-workspace/' + sectionSlug.slice(0, -9) + '/permissions']);
  }

  showCreateSectionFormMobile() {
    this.clearAllStatusText();
    this.showCreateSectionFormMb = true;
  }

  hideCreateSectionFormMobile() {
    this.clearAllStatusText();
    this.showCreateSectionFormMb = false;
  }

  clearAllStatusText() {
    this.errorText = null;
    this.successText = null;
  }


  // For handling expansion panel
  setSectionStep(step: number) {
    this.sectionStep = step;
  }

  isSectionListEmpty() {
    if (this.sectionList) {
      return this.sectionList.length === 0;
    } else {
      return false;
    }
  }

  closeErrorText() {
    this.errorText = null;
  }

  closeSuccessText() {
    this.successText = null;
  }

  deleteSectionClicked(classObject: SectionDetailsResponse) {
    this.subscribedDialogData = this.uiService.dialogData$.subscribe(
      result => {
        if (result) {
          this.deleteSection(classObject);
        }
        this.unsubscribeDialogData();
      }
    )
    const header = 'Are you sure you want to delete ' + classObject.name.charAt(0).toUpperCase() + classObject.name.substr(1).toLowerCase() + ' ?';
    this.uiService.openDialog(header, 'Cancel', 'Delete');
  }

  deleteSection(sectionObject: SectionDetailsResponse) {
    // this.instituteApiService.deleteSection(sectionObject.class_slug).subscribe(
    //   () => {
    //     this.uiService.showSnackBar('Deleted section ' + sectionObject.name.toUpperCase() + ' successfully!', 2000);
    //     this.sectionList.splice(this.sectionList.indexOf(sectionObject), 1);
    //   }
    // )
  }

  unsubscribeDialogData() {
    if (this.subscribedDialogData) {
      this.subscribedDialogData.unsubscribe();
    }
  }

  sectionHasIncharge(inchargeList: SectionInchargeDetails[]) {
    if (inchargeList.length > 0) {
      return true;
    } else {
      return false;
    }
  }

  userIsAdmin() {
    if (sessionStorage.getItem(currentInstituteRole) === INSTITUTE_ROLE_REVERSE['Admin']) {
      return true;
    } else {
      return false;
    }
  }

  userIsSelf(id: number) {
    if (sessionStorage.getItem(userId) === id.toString()) {
      return true;
    } else {
      return false;
    }
  }
}
