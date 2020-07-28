import { SharedModule } from './../shared/shared.module';
import { NgModule } from '@angular/core';
import { CommonModule } from '@angular/common';
import { SchoolMaterialWorkspaceModule } from './material.school.workspace.module';
import { LicenseModule } from './../license/license.module';
import { SchoolWorkspaceRoutingModule, schoolWorkspaceRoutingComponents } from './school-workspace-routing.module';
import { FormsModule, ReactiveFormsModule } from '@angular/forms';
import { FlexLayoutModule } from '@angular/flex-layout';
import { MatDatepickerModule } from '@angular/material/datepicker';
import { MatNativeDateModule, DateAdapter, MAT_DATE_FORMATS } from '@angular/material/core';
import { APP_DATE_FORMATS, AppDateAdapter } from '../format-datepicker';
import { InAppDataTransferService } from '../in-app-data-transfer.service';
import { ApiService } from '../services/api.service';
import { WindowRefService } from './../services/window-ref.service';


@NgModule({
  declarations: [schoolWorkspaceRoutingComponents],
  imports: [
    CommonModule,
    SchoolWorkspaceRoutingModule,
    SchoolMaterialWorkspaceModule,
    LicenseModule,
    FormsModule,
    ReactiveFormsModule,
    FlexLayoutModule,
    MatDatepickerModule,
    MatNativeDateModule,
    SharedModule
  ],
  providers: [
    ApiService,
    InAppDataTransferService,
    WindowRefService,
    {provide: DateAdapter, useClass: AppDateAdapter},
    {provide: MAT_DATE_FORMATS, useValue: APP_DATE_FORMATS}
  ]
})
export class SchoolWorkspaceModule { }
