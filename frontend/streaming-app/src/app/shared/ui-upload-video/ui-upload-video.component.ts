import { MediaMatcher } from '@angular/cdk/layout';
import { FormGroup, FormBuilder, Validators } from '@angular/forms';
import { Component, OnInit, EventEmitter, Output, Input, OnDestroy } from '@angular/core';
import { formatDate } from '../../format-datepicker';
import { Subscription, Observable } from 'rxjs';
import { getFileSize } from '../utilityFunctions';
import { STUDY_MATERIAL_CONTENT_TYPE_REVERSE } from 'src/constants';


@Component({
  selector: 'app-ui-upload-video',
  templateUrl: './ui-upload-video.component.html',
  styleUrls: ['./ui-upload-video.component.css']
})
export class UiUploadVideoComponent implements OnInit, OnDestroy {

  mq: MediaQueryList;
  uploadForm: FormGroup;
  showIndicator: boolean;
  progress = 0;
  @Input() showTargetDate: boolean;
  @Output() formData = new EventEmitter<any>();
  @Output() fileError = new EventEmitter<string>();
  @Input() formEvent: Observable<String>;
  private formEventSubscription: Subscription;
  @Input() uploadProgressEvent: Observable<{loaded: number, total: number}>;
  private progressEventSubscription: Subscription;
  totalFileSize: number;
  loadedFileSize: number;

  constructor(
    private media: MediaMatcher,
    private formBuilder: FormBuilder
  ) {
    this.mq = this.media.matchMedia('(max-width: 600px)');
  }

  ngOnInit(): void {
    this.uploadForm = this.formBuilder.group({
      title: [null, [Validators.maxLength(30), Validators.required]],
      file: [null, [Validators.required]],
      target_date: [null]
    });
    this.formEventSubscription = this.formEvent.subscribe(
      (data: string) => {
        if (data === 'ENABLE') {
          this.showIndicator = false;
          this.uploadForm.enable();
        } else if (data === 'DISABLE') {
          this.showIndicator = true;
          this.uploadForm.disable();
        } else if (data === 'RESET') {
          this.showIndicator = false;
          this.uploadForm.reset();
          this.uploadForm.enable();
        }
      }
    );
    this.progressEventSubscription = this.uploadProgressEvent.subscribe(
      (result: {loaded: number, total: number}) => {
        this.progress = Math.round(100 * result.loaded / result.total);
        this.loadedFileSize = result.loaded;
        this.totalFileSize = result.total;
      }
    );
  }

  upload() {
    const file: File = (<HTMLInputElement>document.getElementById('video-file')).files[0];

    if (!file.type.includes('video/mp4') && !file.type.includes('video/mov') && !file.type.includes('video/avi') && !file.type.includes('video/flv')) {
      this.fileError.emit('Only .mp4, .mov, .avi and .flv video formats are supported.');
      this.uploadForm.patchValue({
        file: null
      });
    } else {
      let data = {};
      data['title'] = this.uploadForm.value.title;
      data['file'] = file;
      if (this.uploadForm.value.target_date) {
        data['target_date'] = formatDate(this.uploadForm.value.target_date);
      }
      data['size'] = file.size / 1000000000;
      data['content_type'] = STUDY_MATERIAL_CONTENT_TYPE_REVERSE['VIDEO'];
      this.formData.emit(data);
    }
  }

  getFileSize_(size: number) {
    return getFileSize(size);
  }

  ngOnDestroy() {
    if (this.formEventSubscription) {
      this.formEventSubscription.unsubscribe();
    }
    if (this.progressEventSubscription) {
      this.progressEventSubscription.unsubscribe();
    }
  }

}
