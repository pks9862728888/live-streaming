import { FormGroup, ValidatorFn, ValidationErrors, FormControl, AbstractControl } from '@angular/forms';
import { QUESTION_SECTION_VIEW_TYPE } from 'src/constants';

// Generates error if passwords do not match.
export const passwordMatchValidator: ValidatorFn = (control: FormGroup): ValidationErrors | null => {
    const password = control.get('password');
    const confirmPassword = control.get('confirmPassword');

    return password.value === confirmPassword.value ? null : { mismatch: true };

};

// Generates error if username and password are similar
export const usernamePasswordValidator: ValidatorFn = (control: FormGroup): ValidationErrors | null => {
    const username = control.get('username');
    const password = control.get('password');

    if (username.pristine || password.pristine) {
        return null;
    }

    return password.value === username.value ? { usernamePasswordSame : true } : null;
};


export function postiveIntegerValidator(control: AbstractControl): {[key: string]: boolean} | null  {
  if (control.pristine || Number.isInteger(control.value) && control.value > 0) {
    return null;
  }
  return { postiveIntegerValidator: true };
}


export function isNumberValidator(control: AbstractControl): {[key: string]: boolean} | null {
  if (control.pristine || !Number.isNaN(control.value)) {
    return null;
  }
  return { isNumberValidator: true };
}

export function islengthWithin20Validator(control: AbstractControl): {[key: string]: boolean} | null {
  if (control.pristine || control.value.length <= 20) {
    return null;
  }
  return { islengthWithin20Validator: true };
}

export const addQuestionFormValidator: ValidatorFn = (control: FormGroup): ValidationErrors | null => {
  const noOfQuestionToAttempt = control.get('no_of_question_to_attempt').value;
  const answerAllQuestions = control.get('answer_all_questions').value;
  const view = control.get('view').value;

  if (!Number.isInteger(noOfQuestionToAttempt)) {
    return { numberOfQuestionToAnswerError: true };
  }

  if (view === QUESTION_SECTION_VIEW_TYPE.MULTIPLE_QUESTION) {
    if (answerAllQuestions && noOfQuestionToAttempt === 0) {
      return null;
    } else {
      return { numberOfQuestionToAnswerError: true };
    }
  }
  return null;
};
