import { MatTooltipModule } from '@angular/material/tooltip';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatCardModule } from '@angular/material/card';
import { MatExpansionModule } from '@angular/material/expansion';
import { MatSidenavModule } from '@angular/material/sidenav';
import { MatIconModule } from '@angular/material/icon';
import { MatButtonModule } from '@angular/material/button';
import { NgModule } from '@angular/core';
import { MatListModule } from '@angular/material/list';
import { MatSlideToggleModule } from '@angular/material/slide-toggle';


@NgModule({
  imports: [
    MatButtonModule,
    MatIconModule,
    MatSidenavModule,
    MatListModule,
    MatExpansionModule,
    MatCardModule,
    MatProgressSpinnerModule,
    MatTooltipModule,
    MatSlideToggleModule
  ],
  exports: [
    MatButtonModule,
    MatIconModule,
    MatSidenavModule,
    MatListModule,
    MatExpansionModule,
    MatCardModule,
    MatProgressSpinnerModule,
    MatTooltipModule,
    MatSlideToggleModule
  ]
})
export class MaterialClassWorkspaceModule {}
