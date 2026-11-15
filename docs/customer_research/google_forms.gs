/**
 * 핏장 고객 검증용 Google Forms 생성기
 *
 * 1. 아래 CONFIG를 수정합니다.
 * 2. Apps Script에서 createAllFitjangForms를 실행합니다.
 * 3. 실행 로그의 편집 주소를 열어 검토하고 직접 게시합니다.
 *
 * 실행할 때마다 새 설문지가 만들어집니다. 기존 설문을 수정하지 않습니다.
 * 후속 조사만 하나 더 만들려면 FOLLOWUP_TARGET을 바꾼 후
 * createFollowUpForm만 실행하세요.
 */

const CONFIG = {
  TEAM_NAME: '[팀명]',
  CONTACT: '[팀 연락 방법]',
  RECRUIT_FORM_URL: '', // 선택: 별도로 만든 인터뷰·체험 신청 폼 URL
  FOLLOWUP_TARGET: '현재 핏장', // 예: '신규 기능 체험판'
};

/** 본 설문과 7일 후 추적 설문을 각각 새로 만듭니다. */
function createAllFitjangForms() {
  createMainSurvey();
  createFollowUpForm();
}

/** 본 설문만 새로 만듭니다. */
function createMainSurvey() {
  // 게시 전에 문구와 접근 대상을 검토하도록 미게시 상태로 생성합니다.
  const form = FormApp.create('대학생의 코디 선택과 디지털 옷장 이용 경험 조사', false);
  logCreatedForm_('본 설문: 생성 직후', form);
  configureForm_(form);
  form.setDescription([
    `안녕하세요. 대학생 대상 옷장·코디 서비스 ‘핏장’을 만드는 ${CONFIG.TEAM_NAME}입니다.`,
    '',
    '대학생들이 실제로 옷을 고르는 방법과 디지털 옷장 서비스의 이용 경험을 알아보기 위해 설문을 진행합니다. 핏장이나 에이클로젯을 사용하지 않았어도 참여할 수 있습니다.',
    '',
    '좋은 평가뿐 아니라 필요하지 않다고 느낀 점도 서비스 방향을 결정하는 데 도움이 됩니다. 정답은 없으며, 최근 실제 경험을 기준으로 답해주세요.',
    '',
    '약 7~12분이 소요됩니다. 이름·학번·계정 정보·사진은 수집하지 않습니다. 응답은 서비스 기획과 고객 검증에 활용합니다. 참여는 자율적이며 언제든 중단할 수 있습니다.',
    '',
    `문의: ${CONFIG.CONTACT}`,
  ].join('\n'));

  const nextSteps = CONFIG.RECRUIT_FORM_URL.trim()
    ? `후속 인터뷰나 체험에 참여하고 싶은 분은 별도 신청 폼을 이용해주세요.\n${CONFIG.RECRUIT_FORM_URL.trim()}`
    : '후속 인터뷰나 체험의 신청 방법은 운영팀의 별도 안내를 확인해주세요.';
  form.setConfirmationMessage(`참여해주셔서 감사합니다. 응답은 서비스 방향을 결정하는 데 활용하겠습니다.\n\n${nextSteps}`);

  // 첫 페이지에는 PageBreak 대신 SectionHeader를 사용합니다.
  form.addSectionHeaderItem().setTitle('A. 평소 코디 선택 경험');
  choice_(form, 'Q1. 현재 학적 상태를 선택해주세요.', [
    '대학교 학부 1~2학년 재학', '대학교 학부 3학년 이상 재학',
    '대학교 학부 휴학', '대학원 재학·휴학', '그 외',
  ]);
  choice_(form, 'Q2. 평소 옷차림이나 코디에 어느 정도 관심이 있나요?', [
    '전혀 관심이 없다', '별로 관심이 없다', '보통이다', '어느 정도 관심이 있다', '매우 관심이 있다',
  ]);
  choice_(form, 'Q3. 최근 7일 동안, 외출할 때 어떤 옷을 입을지 결정하기 어려웠던 날은 며칠인가요?', [
    '0일', '1~2일', '3~4일', '5일 이상', '외출할 일이 없었다', '기억나지 않는다',
  ]);
  // 분기 목적지 페이지는 아래에서 생성한 뒤 선택지를 설정합니다.
  const q4 = form.addMultipleChoiceItem()
    .setTitle('Q4. 최근 30일 동안, 다른 사람의 옷차림이나 코디 사진을 저장·캡처한 횟수는 얼마나 되나요?')
    .setHelpText('SNS 저장, 스크린샷, 사진 다운로드 등을 포함합니다. 옷 한 벌의 상품 사진만 저장한 경우는 제외해주세요.')
    .setRequired(true);

  const pageB = form.addPageBreakItem().setTitle('B. 가장 최근에 저장한 코디')
    .setHelpText('가장 최근에 저장한 코디 사진 한 장을 떠올리고 답해주세요. 사진을 제출할 필요는 없습니다.');
  choice_(form, 'Q5. 그 사진을 저장한 가장 큰 이유는 무엇인가요?', [
    '비슷한 옷차림을 직접 해보고 싶어서', '사진 속 옷을 구매하고 싶어서',
    '나중에 코디를 고를 때 참고하려고', '사진이나 분위기 자체가 마음에 들어서', '다른 사람에게 보여주려고',
  ], { other: true });
  choice_(form, 'Q6. 그 사진을 저장한 뒤, 현재까지 어떤 행동으로 이어졌나요?', [
    '가지고 있던 옷으로 참고해서 입었다', '옷을 새로 사거나 빌려서 참고해 입었다',
    '비슷하게 입어보려 했지만, 결국 입지 않았다', '아직 시도하지 않았지만 앞으로 입어볼 생각이다',
    '직접 따라 입을 생각은 없다', '기억나지 않는다',
  ], { help: '가장 가까운 결과 하나를 선택해주세요.' });
  choice_(form, 'Q7. 그 사진을 참고해 입으려 할 때 가장 어려웠던 점은 무엇인가요?', [
    '내 옷 중 어떤 옷을 조합해야 할지 모르겠다', '비슷한 역할을 하는 옷이 없다',
    '내가 입었을 때 어떤 모습일지 예상하기 어렵다', '사진과 다른 옷으로 바꿔도 괜찮을지 모르겠다',
    '날씨나 실제 일정에 맞추기 어렵다', '옷을 꺼내 비교하고 입어보는 과정이 번거롭다',
    '특별히 어려운 점이 없었다', '시도하지 않아 판단하기 어렵다',
  ], { other: true });
  paragraph_(form, 'Q8. 그때의 상황이나 해결 방법을 조금 더 설명해주세요.', false,
    '어떤 모습으로 입고 싶었는지, 어디서 막혔는지, 결국 어떻게 했는지 자유롭게 적어주세요.');

  const pageC = form.addPageBreakItem().setTitle('C. 현재 핏장 이용 여부');
  const q9 = form.addMultipleChoiceItem()
    .setTitle('Q9. 지금까지 핏장을 어떻게 경험했나요?')
    .setRequired(true);

  const pageD = form.addPageBreakItem().setTitle('D. 현재 핏장 직접 사용 경험')
    .setHelpText('현재 제공되는 핏장에 대해 답해주세요. 뒤에서 소개할 새 기능에 대한 질문은 별도로 있습니다.');
  choice_(form, 'Q10. 핏장을 처음 직접 사용한 것은 언제인가요?', [
    '오늘', '1~6일 전', '7~29일 전', '30일 이상 전', '기억나지 않는다',
  ]);
  choice_(form, 'Q11. 처음 사용한 날을 제외하고, 최근 7일 동안 핏장을 다시 사용한 날은 며칠인가요?', [
    '0일', '1일', '2일', '3일 이상', '오늘 처음 사용했다', '기억나지 않는다',
  ]);
  grid_(form, 'Q12. 현재 핏장의 다음 항목에 얼마나 만족하나요?', [
    '옷을 등록하는 과정의 편리함', '추천 코디가 내 취향에 맞는 정도',
    '추천 결과가 나오는 속도', '착샷을 보며 코디를 비교하는 경험',
  ], ['매우 불만족', '불만족', '보통', '만족', '매우 만족', '사용하지 않았거나 판단하기 어려움']);
  choice_(form, 'Q13. 핏장의 추천이 실제 옷차림으로 이어진 적이 있나요?', [
    '추천된 조합을 그대로 입었다', '추천 중 일부를 참고해 입었다',
    '추천은 봤지만 실제로 입지는 않았다', '사용한 뒤 아직 입어볼 기회가 없었다', '추천 결과를 확인하지 못했다',
  ]);
  choice_(form, 'Q14. 앞으로 7일 안에, 현재 핏장을 다시 사용할 의향이 있나요?', intention_());
  choice_(form, 'Q15. 현재 핏장을 다시 사용하는 것을 가장 망설이게 하는 이유는 무엇인가요?', [
    '추천 코디가 마음에 들지 않는다', '착샷이 실제 내 옷과 다르게 보여 판단하기 어렵다',
    '처리나 이미지 로딩을 기다리기 어렵다', '옷을 등록하고 관리하는 수고가 크다',
    '사진을 등록하는 것이 부담스럽다', '기존에 쓰던 방법으로 충분하다',
    '언제 다시 사용해야 할지 잘 모르겠다', '특별히 망설이는 이유가 없다',
  ], { other: true });

  const pageD2 = form.addPageBreakItem().setTitle('D-2. 직접 사용하지 않은 이유');
  choice_(form, 'Q16. 아직 핏장을 직접 사용하지 않은 가장 큰 이유는 무엇인가요?', [
    '이번 설문에서 처음 알게 됐다', '알고 있었지만 사용해볼 시간이 없었다',
    '어떤 도움이 되는지 잘 모르겠다', '옷이나 사진을 등록하는 과정이 부담스럽다',
    '현재 사용하는 방법이나 서비스로 충분하다', '이와 같은 서비스가 필요하지 않다',
  ], { other: true });

  const pageE = form.addPageBreakItem().setTitle('E. 에이클로젯 이용 경험');
  const q17 = form.addMultipleChoiceItem()
    .setTitle('Q17. 핏장과 에이클로젯의 옷장 또는 코디 추천 기능을 직접 사용해본 경험이 있나요?')
    .setHelpText('앱 소개·영상·다른 사람의 화면만 본 경우는 직접 사용에 포함하지 않습니다.')
    .setRequired(true);

  const pageF = form.addPageBreakItem().setTitle('F. 두 서비스를 직접 사용한 사람의 비교')
    .setHelpText('현재 직접 경험한 기능만 비교해주세요. 사용해보지 않은 항목은 ‘비교하기 어려움’을 선택해주세요.');
  choice_(form, 'Q18. ‘내가 가진 옷으로 입을 코디를 정하는 일’에 다시 서비스를 사용한다면, 어느 쪽을 선택하겠나요?', [
    '핏장', '에이클로젯', '상황에 따라 다르다', '둘 사이에 특별한 선호가 없다',
    '둘 다 사용하지 않을 것 같다', '비교하기 어렵다',
  ]);
  grid_(form, 'Q19. 직접 사용한 경험을 기준으로 비교해주세요.', [
    '옷을 등록하기 편한 정도', '실제로 입고 싶은 코디를 찾는 데 도움이 되는 정도',
    '의류 이미지를 보며 코디를 비교하기 편한 정도',
  ], ['핏장이 더 좋음', '비슷함', '에이클로젯이 더 좋음', '해당 항목은 비교하기 어려움']);
  paragraph_(form, 'Q20. 두 서비스 중 하나를 선택하게 만드는 구체적인 차이가 있었다면 적어주세요.', false,
    '실제로 사용했던 상황이나 기능을 적어주세요. 차이를 느끼지 못했다면 그렇게 적어도 괜찮습니다.');

  const pageG = form.addPageBreakItem().setTitle('G. 새로운 기능 방향에 대한 의견')
    .setHelpText([
      '다음은 개발을 검토 중인 기능입니다. 현재 핏장에서 이미 제공하거나 효과가 확인된 기능이라는 뜻은 아닙니다.',
      '',
      '‘참고 코디를 내 옷으로 구성하기’',
      '① 사용자가 참고하고 싶은 코디 사진을 선택합니다.',
      '② 사진에서 따라 하고 싶은 색 조합이나 분위기를 지정합니다.',
      '③ 서비스가 등록된 본인 옷 중 상의·하의·아우터를 조합해 후보를 제안합니다.',
      '④ 필요한 경우 후보의 2D 착샷을 참고해 입을 조합을 고릅니다.',
      '',
      '먼저 본인 옷을 등록해야 합니다. 가진 옷에 따라 참고 사진과 다른 조합이 나올 수 있으며, 착샷은 정확한 사이즈나 착용감을 보장하지 않는 참고 이미지입니다.',
    ].join('\n'));
  choice_(form, 'Q21. 최근 30일을 돌아봤을 때, 이 기능을 사용할 만한 상황이 몇 번 있었나요?', [
    '없었다', '1번', '2~3번', '4번 이상', '설명만으로는 판단하기 어렵다',
  ]);
  choice_(form, 'Q22. 다음에 마음에 드는 코디 사진을 발견한다면, 가장 먼저 어떤 방법을 사용하겠나요?', [
    '내 옷을 직접 꺼내 조합해본다', '친구나 지인에게 의견을 구한다',
    '현재 쓰는 앱이나 서비스에서 도움을 받는다', '위에서 설명한 새 기능을 사용해본다',
    '사진 속 옷이나 비슷한 옷을 찾아본다', '저장하거나 보기만 하고 별도로 행동하지 않는다',
  ], { other: true });
  choice_(form, 'Q23. 이 기능이 현재 사용하는 방법에서는 얻기 어려웠던 도움을 줄 것 같나요?', [
    '전혀 그렇지 않다', '별로 그렇지 않다', '어느 쪽도 아니다', '어느 정도 그렇다', '매우 그렇다', '아직 판단하기 어렵다',
  ]);
  choice_(form, 'Q24. 처음 사용하려면 자주 입는 옷 8벌을 등록하는 데 약 10분이 필요하다고 가정할 때, 직접 등록해볼 의향이 있나요?', [
    '전혀 없다', '별로 없다', '어느 쪽도 아니다', '어느 정도 있다', '매우 있다', '이미 자주 입는 옷을 충분히 등록했다',
  ], { help: '조사용 가정이며 실제 소요 시간을 약속하는 것은 아닙니다. 이미 충분한 옷을 등록했다면 마지막 항목을 선택해주세요.' });
  choice_(form, 'Q25. 이 기능이 무료 체험으로 제공된다면, 제공된 뒤 첫 7일 안에 사용해볼 의향이 있나요?', intention_());
  choice_(form, 'Q26. 이 기능을 사용하지 않거나 중단하게 될 가장 큰 이유는 무엇일 것 같나요?', [
    '참고 사진을 바탕으로 코디할 필요가 없다', '이미 쓰는 방법이나 서비스로 충분하다',
    '옷을 등록하는 수고가 크다', '내 옷이 충분하지 않아 쓸 만한 조합이 나오지 않을 것 같다',
    '원하는 분위기를 제대로 반영하지 못할 것 같다', '착샷과 실제 옷차림이 다를 것 같다',
    '사진을 등록하는 것이 부담스럽다', '결과를 기다리는 시간이 부담스러울 것 같다',
    '특별히 떠오르는 이유가 없다',
  ], { other: true });
  paragraph_(form, 'Q27. 이 기능을 사용할 구체적인 상황이 떠오른다면 적어주세요.', false,
    '언제, 어떤 코디를 정할 때 사용할지 적어주세요. 사용할 상황이 떠오르지 않는다면 ‘없음’이라고 적어도 괜찮습니다.');

  form.addPageBreakItem().setTitle('H. 후속 참여');
  choice_(form, 'Q28. 향후 진행할 인터뷰나 기능 체험에 참여할 의향이 있나요?', [
    '인터뷰에 참여할 의향이 있다', '기능 체험에 참여할 의향이 있다',
    '둘 다 참여할 의향이 있다', '참여하지 않겠다',
  ]);
  textCode_(form, 'Q29. 후속 조사에 참여하려면 응답 연결용 코드를 만들어 적어주세요.', false,
    '본인 정보와 관련 없는 영문·숫자 8자로 만들어 따로 보관해주세요. 이후 설문에서 같은 코드를 입력하면 응답을 연결합니다. 학번·생일·전화번호·계정 비밀번호는 사용하지 마세요.');

  // 모든 선택지에 명시적으로 이동 대상을 지정합니다.
  q4.setChoices([
    q4.createChoice('0회', pageC),
    q4.createChoice('1~2회', pageB),
    q4.createChoice('3~5회', pageB),
    q4.createChoice('6회 이상', pageB),
    q4.createChoice('기억나지 않는다', pageC),
  ]);
  q9.setChoices([
    q9.createChoice('직접 기능을 사용해봤다', pageD),
    q9.createChoice('직접 사용하려 했지만 오류나 어려움 때문에 중단했다', pageD),
    q9.createChoice('다른 사람의 시연이나 화면만 봤다', pageD2),
    q9.createChoice('아직 접속하거나 사용해보지 않았다', pageD2),
  ]);
  q17.setChoices([
    q17.createChoice('두 서비스 모두 직접 사용해봤다', pageF),
    q17.createChoice('핏장만 직접 사용해봤다', pageG),
    q17.createChoice('에이클로젯만 직접 사용해봤다', pageG),
    q17.createChoice('두 서비스 모두 직접 사용해보지 않았다', pageG),
  ]);

  // Apps Script의 PageBreak 이동은 해당 PageBreak '이전 페이지'에 적용됩니다.
  // 따라서 D 다음에 놓인 D-2 페이지 나누기를 이용해 D 완료 후 E로 건너뜁니다.
  pageD2.setGoToPage(pageE);

  logCreatedForm_('본 설문: 생성 완료', form);
  return form.getEditUrl();
}

/** CONFIG.FOLLOWUP_TARGET에 지정된 대상의 7일 추적 설문을 새로 만듭니다. */
function createFollowUpForm() {
  const target = CONFIG.FOLLOWUP_TARGET.trim() || '참여한 서비스';
  const form = FormApp.create(`${target} 이용 후 7일 경험 확인`, false);
  logCreatedForm_('7일 후 설문: 생성 직후', form);
  configureForm_(form);
  form.setDescription([
    `이번 추적 대상: ${target}`,
    '지난 체험 이후 실제 사용 경험을 확인하는 짧은 설문입니다. 다시 사용하지 않았어도 중요한 응답이므로 그대로 답해주세요. 약 2~3분이 소요됩니다.',
    '본 설문과 연결하려면 이전에 만든 8자리 응답 연결 코드를 입력해주세요.',
    `문의: ${CONFIG.CONTACT}`,
  ].join('\n\n'));
  form.setConfirmationMessage('참여해주셔서 감사합니다. 실제 사용 경험은 서비스 방향을 정하는 데 활용하겠습니다.');
  textCode_(form, 'F1. 이전 설문에서 사용한 응답 연결 코드를 입력해주세요.', true,
    '이전에 직접 만든 영문·숫자 8자리 코드를 입력해주세요.');
  choice_(form, 'F2. 처음 체험한 날을 제외하고, 지난 7일 동안 이 서비스를 사용한 날은 며칠인가요?', [
    '0일', '1일', '2일', '3일 이상', '첫 체험 자체를 완료하지 못했다', '기억나지 않는다',
  ]);
  choice_(form, 'F3. 다시 사용했다면, 가장 최근에 들어온 주된 계기는 무엇인가요?', [
    '실제로 입을 코디를 정해야 해서', '따라 입고 싶은 코디 사진을 발견해서', '기능이나 결과가 궁금해서',
    '연구팀의 요청이나 안내를 받아서', '체험 보상이나 참여 조건을 충족하려고', '다시 사용하지 않았다',
  ], { other: true });
  choice_(form, 'F4. 지난 7일 동안 서비스가 제안한 코디를 참고해 실제로 입은 날은 며칠인가요?', [
    '0일', '1일', '2일', '3일 이상', '코디 제안을 확인하지 못했다', '기억나지 않는다',
  ]);
  choice_(form, 'F5. 사용하지 않았거나 사용을 중단한 주된 이유는 무엇인가요?', [
    '사용할 상황이 없었다', '다른 방법이 더 편했다', '원하는 조합을 얻지 못했다',
    '옷 등록이나 준비가 번거로웠다', '오류나 대기시간 때문에 진행하기 어려웠다',
    '결과 이미지를 믿고 선택하기 어려웠다', '서비스가 떠오르지 않았다', '특별한 문제 없이 사용했다',
  ], { other: true });
  choice_(form, 'F6. 앞으로 7일 안에 다시 사용할 의향이 있나요?', intention_());
  paragraph_(form, 'F7. 실제 사용에 가장 도움이 됐거나, 다시 쓰지 않게 만든 경험을 하나 적어주세요.', false);
  logCreatedForm_('7일 후 설문: 생성 완료', form);
  return form.getEditUrl();
}

/** 설문 설정: 이메일 미수집, 로그인 강제 없음, 결과 요약 비공개. */
function configureForm_(form) {
  form.setCollectEmail(false);
  form.setLimitOneResponsePerUser(false);
  form.setProgressBar(true);
  form.setShuffleQuestions(false);
  form.setPublishingSummary(false);
  form.setAllowResponseEdits(false);
  form.setShowLinkToRespondAgain(false);
}

function choice_(form, title, choices, options) {
  options = options || {};
  const item = form.addMultipleChoiceItem()
    .setTitle(title).setChoiceValues(choices).setRequired(options.required !== false);
  if (options.help) item.setHelpText(options.help);
  if (options.other) item.showOtherOption(true);
  return item;
}

function grid_(form, title, rows, columns) {
  return form.addGridItem().setTitle(title).setRows(rows).setColumns(columns).setRequired(true);
}

function paragraph_(form, title, required, help) {
  const item = form.addParagraphTextItem().setTitle(title).setRequired(required);
  if (help) item.setHelpText(help);
  return item;
}

function textCode_(form, title, required, help) {
  const validation = FormApp.createTextValidation()
    .requireTextMatchesPattern('^[A-Za-z0-9]{8}$')
    .setHelpText('영문자 또는 숫자 8자리를 입력해주세요.').build();
  return form.addTextItem().setTitle(title).setHelpText(help).setRequired(required).setValidation(validation);
}

function intention_() {
  return ['전혀 없다', '별로 없다', '어느 쪽도 아니다', '어느 정도 있다', '매우 있다', '아직 판단하기 어렵다'];
}

function logCreatedForm_(label, form) {
  // 중간에 오류가 나도 이미 생성된 설문을 찾을 수 있게 편집 URL을 즉시 남깁니다.
  console.log(`${label} 편집 주소: ${form.getEditUrl()}`);
  console.log(`${label} 응답 주소(게시 후 사용): ${form.getPublishedUrl()}`);
}
