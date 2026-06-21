export const commonTranslations = {
  en: {
    back: "Back",
    language: "Language",
    english: "English",
    french: "Français",
    chooseFile: "Choose file",
    fileAccepted: "File accepted",
    formatPolicy: "Format policy",
    previewArea: "Preview area",
    outputFormat: "Output format",
    generating: "Generating...",
    converting: "Converting...",
    translating: "Translating...",
    explaining: "Explaining...",
    summarize: "Summarize",
    explain: "Explain",
    translate: "Translate",
    convert: "Convert",
    download: "Download output",
    transcribe: "Transcribe",
    transcribing: "Transcribing...",
  },
  fr: {
    back: "Retour",
    language: "Langue",
    english: "English",
    french: "Français",
    chooseFile: "Choisir un fichier",
    fileAccepted: "Fichier accepté",
    formatPolicy: "Règles de format",
    previewArea: "Zone d’aperçu",
    outputFormat: "Format de sortie",
    generating: "Génération...",
    converting: "Conversion...",
    translating: "Traduction...",
    explaining: "Explication...",
    summarize: "Résumer",
    explain: "Expliquer",
    translate: "Traduire",
    convert: "Convertir",
    download: "Télécharger le fichier",
    transcribe: "Transcrire",
    transcribing: "Transcription...",
  },
};


export const verifyEmailRequiredPageTranslations = {
  en: {
    badge: "Email verification required",
    title: "Verify your email",
    description:
      "We sent you a verification link. Open the email and complete verification to activate your account.",
    nextStep:
      "After verification, return to ReDOCX and continue from where you left off.",
  },
  fr: {
    badge: "Vérification de l’e-mail requise",
    title: "Vérifiez votre adresse e-mail",
    description:
      "Nous vous avons envoyé un lien de vérification. Ouvrez l’e-mail et terminez la vérification pour activer votre compte.",
    nextStep:
      "Après la vérification, revenez sur ReDOCX et reprenez là où vous vous étiez arrêté.",
  },
};

export const actionCardTranslations = {
  en: {
    open: "Open",
    accountRequired: "Requires sign in",
    soon: "Soon",
  },
  fr: {
    open: "Ouvrir",
    accountRequired: "Connexion requise",
    soon: "Bientôt",
  },
};

export const homePageTranslations = {
  en: {
    back: "back",
    signIn: "Sign In",
    signUp: "Sign Up",
    loading: "Loading...",
    signedInAs: "Signed in as",
    logout: "Logout",
    logoutConfirm: {
      title: "Are you sure you want to Logout?",
      yes: "Yes",
      returnDashboard: "Return back to Dashboard",
    },
    changePassword: {
      label: "Change password",
      sending: "Sending...",
      success: "Password reset email sent. You are being signed out.",
      error: "Could not start password change. Please try again.",
    },
    deleteAccount: {
      label: "Delete my account",
      title: "Delete your account?",
      description:
        "This permanently deletes your ReDOCX account and signs you out. This action cannot be undone.",
      confirm: "Delete my account",
      cancel: "Cancel",
      deleting: "Deleting...",
      error: "Could not delete your account. Please try again.",
    },
    languageLabel: "Language",
    english: "English",
    french: "Français",
    settings: "Settings",
    appearance: "Appearance",
    light: "Light",
    dark: "Dark",
    systemDefault: "System Default",
    appName: "ReDOCX",
    dashboardGreeting: "Hello, how can I help you?",
    aiFeaturesTitle: "Explore",
    aiFeaturesCompactTitle: "AI",
    manageTitle: "Manage",
    manageCompactTitle: "Manage",
    soon: "Soon",
    requiresSignIn: "Requires sign in",
    teamAccessModal: {
      signInAndUpgrade: "Sign in and upgrade to continue",
      upgradeToTeamPlan: "Upgrade to Business or Enterprise to continue",
      close: "Close",
    },
    teamInvitationToast: {
      title: "Team invitation",
      body: "You have been invited to join {organization} on the {plan} plan.",
      fallbackOrganization: "this team",
      accept: "Accept",
      accepting: "Accepting...",
      accepted: "Invitation accepted. Your account is now on the team plan.",
      deny: "Deny",
      denying: "Denying...",
      denied: "Invitation denied.",
    },
    manageActions: [
      {
        key: "dashboard",
        name: "Dashboard",
        route: "/",
      },
      {
        key: "apiKeys",
        name: "API Keys",
      },
      {
        key: "projectsTeam",
        name: "Projects & Team",
        route: "/team",
      },
      {
        key: "billing",
        name: "Billing & Upgrade",
        route: "/billing",
      },
    ],
    enabledActions: [
      {
        key: "convert",
        name: "File Conversion",
        route: "/convert",
        description: "Convert files and documents to the formats you need",
      },
      {
        key: "summarize",
        name: "Summarize",
        route: "/summarize",
        description: "Turn long content into sharp, useful highlights.",
      },
      {
        key: "grammar",
        name: "Grammar Correct",
        route: "/grammar",
        description: "Polish your writing with clean, confident corrections.",
      },
      {
        key: "translate",
        name: "Translate",
        route: "/translate",
        description: "Translate text naturally across multiple languages.",
      },
      {
        key: "explain",
        name: "Explain",
        route: "/explain",
        description: "Break down difficult ideas into simple explanations.",
      },
    ],
    lockedActions: [
      {
        key: "transcribe",
        name: "Speech to Text",
        route: "/transcribe",
        description: "Convert speech to accurate text",
      },
      {
        key: "questions",
        name: "Generate Questions",
        route: "/questions",
        description: "Create smart questions from notes, text, or topics.",
      },
      {
        key: "redact",
        name: "Redaction",
        route: "/redact",
        description:
          "Redact sensitive data and information from documents preserving the original layout and structure",
      },
      {
        key: "mask",
        name: "Data Masking",
        route: "/mask",
        description:
          "Mask sensitive data and information keeping the document readable and usable",
      },
      {
        key: "compliance",
        name: "Compliance",
        route: "/compliance",
        description: "Check documents against compliance rules",
      },
      {
        key: "eSignature",
        name: "E-Signature",
        route: "/esignature",
        description:
          "Sign PDFs with typed, drawn, or uploaded signatures using your authenticated ReDOCX quota.",
      },
      {
        key: "pdfTools",
        name: "PDF Tools",
        route: "/pdf-tools",
        description:
          "Combine, compress, edit, and split PDFs with authenticated PDF-tool access.",
      },
      {
        key: "textToSpeech",
        name: "Text to Speech",
        comingSoon: true,
        description: "Soon",
      },
      {
        key: "extraction",
        name: "Structured Extraction",
        route: "/extraction",
        description: "Extract key data from documents",
      },
    ],
  },
  fr: {
    back: "retour",
    signIn: "Se connecter",
    signUp: "S’inscrire",
    loading: "Chargement...",
    signedInAs: "Connecté en tant que",
    logout: "Se déconnecter",
    logoutConfirm: {
      title: "Êtes-vous sûr de vouloir vous déconnecter ?",
      yes: "Oui",
      returnDashboard: "Retour au tableau de bord",
    },
    changePassword: {
      label: "Changer le mot de passe",
      sending: "Envoi...",
      success: "E-mail de réinitialisation envoyé. Vous allez être déconnecté.",
      error: "Impossible de lancer le changement de mot de passe. Veuillez réessayer.",
    },
    deleteAccount: {
      label: "Supprimer mon compte",
      title: "Supprimer votre compte ?",
      description:
        "Cette action supprime définitivement votre compte ReDOCX et vous déconnecte. Elle est irréversible.",
      confirm: "Supprimer mon compte",
      cancel: "Annuler",
      deleting: "Suppression...",
      error: "Impossible de supprimer votre compte. Veuillez réessayer.",
    },
    languageLabel: "Langue",
    english: "English",
    french: "Français",
    settings: "Paramètres",
    appearance: "Apparence",
    light: "Clair",
    dark: "Sombre",
    systemDefault: "Par défaut du système",
    appName: "ReDOCX",
    dashboardGreeting: "Bonjour, comment puis-je vous aider ?",
    aiFeaturesTitle: "Explorer",
    aiFeaturesCompactTitle: "IA",
    manageTitle: "Gérer",
    manageCompactTitle: "Gérer",
    soon: "Bientôt",
    requiresSignIn: "Connexion requise",
    teamAccessModal: {
      signInAndUpgrade:
        "Connectez-vous et passez à une offre supérieure pour continuer",
      upgradeToTeamPlan: "Passez à Business ou Enterprise pour continuer",
      close: "Fermer",
    },
    teamInvitationToast: {
      title: "Invitation d’équipe",
      body: "Vous avez été invité à rejoindre {organization} avec le forfait {plan}.",
      fallbackOrganization: "cette équipe",
      accept: "Accepter",
      accepting: "Acceptation...",
      accepted:
        "Invitation acceptée. Votre compte est maintenant sur le forfait d’équipe.",
      deny: "Refuser",
      denying: "Refus...",
      denied: "Invitation refusée.",
    },
    manageActions: [
      {
        key: "dashboard",
        name: "Tableau de bord",
        route: "/",
      },
      {
        key: "apiKeys",
        name: "Clés API",
      },
      {
        key: "projectsTeam",
        name: "Projets & équipe",
        route: "/team",
      },
      {
        key: "billing",
        name: "Facturation & mise à niveau",
        route: "/billing",
      },
    ],
    enabledActions: [
      {
        key: "convert",
        name: "Conversion de fichier",
        route: "/convert",
        description:
          "Convertissez vos fichiers et documents dans les formats dont vouz avez besoin",
      },
      {
        key: "summarize",
        name: "Résumer",
        route: "/summarize",
        description:
          "Transformez un long contenu en points clés utiles et précis.",
      },
      {
        key: "grammar",
        name: "Corriger la grammaire",
        route: "/grammar",
        description:
          "Améliorez votre écriture avec des corrections claires et sûres.",
      },
      {
        key: "translate",
        name: "Traduire",
        route: "/translate",
        description: "Traduisez naturellement du texte dans plusieurs langues.",
      },
      {
        key: "explain",
        name: "Expliquer",
        route: "/explain",
        description: "Décomposez les idées difficiles en explications simples.",
      },
    ],
    lockedActions: [
      {
        key: "transcribe",
        name: "Voix en texte",
        route: "/transcribe",
        description: "Convertir la parole en texte avec précision",
      },
      {
        key: "questions",
        name: "Générer des questions",
        route: "/questions",
        description:
          "Créez des questions intelligentes à partir de notes, de texte ou de sujets.",
      },
      {
        key: "redact",
        name: "Caviardage",
        route: "/redact",
        description:
          "Caviardez les données et informations sensibles dans vos documents tout en préservant la mise en page et la structure d’origine",
      },
      {
        key: "mask",
        name: "Masquage des données",
        route: "/mask",
        description:
          "Masquez les données et informations sensibles tout en conservant un document lisible et exploitable",
      },
      {
        key: "compliance",
        name: "Conformité",
        route: "/compliance",
        description: "Vérifiez les documents selon les règles de conformité",
      },
      {
        key: "eSignature",
        name: "Signature électronique",
        route: "/esignature",
        description:
          "Signez des PDF avec des signatures typées, dessinées ou téléversées avec votre quota ReDOCX authentifié.",
      },
      {
        key: "pdfTools",
        name: "Outils PDF",
        route: "/pdf-tools",
        description:
          "Combinez, compressez, modifiez et divisez des PDF avec l’accès authentifié aux outils PDF.",
      },
      {
        key: "textToSpeech",
        name: "Synthèse vocale",
        comingSoon: true,
        description: "Bientôt",
      },
      {
        key: "extraction",
        name: "Extraction structurée",
        route: "/extraction",
        description: "Extrayez les données clés des documents",
      },
    ],
  },
};
export const convertPageTranslations = {
  en: {
    badge: "Convert documents, files and images",
    title: "Convert files across several formats",
    description: "Upload PDF, Word document, JPG, JPEG, or PNG",
    uploadTitle: "Upload file or document",
    conversionOutput: "Conversion result",
    previewText: "Download appears here after file conversion",

    unsupportedFileType:
      "Unsupported file type: {ext}. Only .pdf, .docx, .jpg, .jpeg, and .png are allowed",
    fileTooLarge: "File is too large, maximum allowed size is {maxSize} MB",
    chooseFileToConvert: "Please choose a file to convert",
    invalidConversion: "This conversion combination is not allowed",
    conversionFailed: "Something went wrong while converting the file",
    missingDownloadUrl:
      "Conversion finished, but the backend did not return a download URL",

    conversionCompleted: "Conversion completed",
    inputFile: "Input file",
    inputExtension: "Input extension",
    outputExtension: "Output extension",
    downloadReady: "Download ready",
    convertedFile: "Converted file",
    outputReadyText: "Your converted file is ready for download",
    detectedType: "Detected type:",
    from: "From",
    convertTo: "Convert to",
    allowedOutputsFor: "Allowed outputs for",
    none: "none",
    conversionLabel: "Conversion:",

    pdfDocument: "PDF document",
    wordDocument: "Word document",
    jpgImage: "JPG image",
    pngImage: "PNG image",
    unknownFile: "Unknown file",
  },
  fr: {
    badge: "Convertir des documents, fichiers et images",
    title: "Convertir des fichiers dans plusieurs formats",
    description:
      "Téléversez un PDF, un document Word, un JPG, un JPEG ou un PNG",
    uploadTitle: "Téléverser un fichier ou un document",
    conversionOutput: "Résultat de la conversion",
    previewText:
      "Le téléchargement apparaîtra ici après la conversion du fichier",

    unsupportedFileType:
      "Type de fichier non pris en charge: {ext}. Seuls les formats .pdf, .docx, .jpg, .jpeg et .png sont autorisés",
    fileTooLarge:
      "Le fichier est trop volumineux, la taille maximale autorisée est de {maxSize} Mo",
    chooseFileToConvert: "Veuillez choisir un fichier à convertir",
    invalidConversion: "Cette combinaison de conversion n’est pas autorisée",
    conversionFailed:
      "Une erreur s’est produite lors de la conversion du fichier",
    missingDownloadUrl:
      "La conversion est terminée, mais le backend n’a pas renvoyé d’URL de téléchargement",

    conversionCompleted: "Conversion terminée",
    inputFile: "Fichier d’entrée",
    inputExtension: "Extension d’entrée",
    outputExtension: "Extension de sortie",
    downloadReady: "Téléchargement prêt",
    convertedFile: "Fichier converti",
    outputReadyText: "Votre fichier converti est prêt à être téléchargé",
    detectedType: "Type détecté:",
    from: "De",
    convertTo: "Convertir vers",
    allowedOutputsFor: "Sorties autorisées pour",
    none: "aucune",
    conversionLabel: "Conversion:",

    pdfDocument: "Document PDF",
    wordDocument: "Document Word",
    jpgImage: "Image JPG",
    pngImage: "Image PNG",
    unknownFile: "Fichier inconnu",
  },
};
export const explainPageTranslations = {
  en: {
    badge: "Explain content clearly",
    title: "Break down difficult content into simple explanations",
    description:
      "Upload a PDF or Word document, or paste inline text. Unsupported files like PNG are rejected automatically, and the output extension always matches the input extension.",

    fileMode: "Upload file",
    textMode: "Inline text",

    uploadTitle: "Upload content to explain",
    allowedFileInputs:
      "Allowed: .pdf and .docx. Rejected automatically: .png, .jpg, and unsupported formats.",
    outputExtensionWillBe: "Output extension will be",

    pasteTextLabel: "Paste text to explain",
    pasteTextPlaceholder: "Paste or type your text here...",
    inlineTextTreatedAs:
      "Inline text is treated as .txt, so the output extension will also be .txt.",

    unsupportedFileType:
      "Unsupported file type: {ext}. Only .pdf and .docx uploads are allowed. PNG and other image formats are rejected.",
    fileTooLarge: "File is too large. Maximum allowed size is {maxSize} MB.",
    explanationFailed: "Something went wrong while generating the explanation.",

    generatingExplanation: "Generating explanation...",
    outputFormatLabel: "Output format:",

    policyTitle: "Format policy",
    policySubtitle: "Strict input and output matching",
    allowedUploadsLabel: "Allowed uploads:",
    inlineInputLabel: "Inline input:",
    rejectedAutomaticallyLabel: "Rejected automatically:",
    outputRuleLabel: "Output rule:",
    inlineInputValue: "treated as .txt",
    rejectedAutomaticallyValue: ".png and all unsupported file types",
    outputRuleValue: "output extension must always equal input extension",

    explanationOutputTitle: "Explanation output",
    previewEmpty:
      "Your generated explanation will appear here. The output extension always mirrors the original input extension.",
    outputExtensionLabel: "Output extension:",

    inlineExplanationIntro: "Explanation generated from inline text.",
    fileExplanationIntro: "Explanation generated from {filename}.",
    rewrittenPreviewText:
      "This content has been rewritten into a simpler explanation while keeping the same output format rule.",
    previewLabel: "Preview:",
    inputExtensionLabel: "Input extension:",
    outputExtensionResultLabel: "Output extension:",
    preservedExtensionMessage:
      "The explanation output preserves the same extension as the original uploaded file.",
  },
  fr: {
    badge: "Expliquer clairement le contenu",
    title: "Décomposez les contenus difficiles en explications simples",
    description:
      "Téléversez un PDF ou un document Word, ou collez du texte inline. Les fichiers non pris en charge comme PNG sont rejetés automatiquement, et l’extension de sortie correspond toujours à l’extension d’entrée.",

    fileMode: "Téléverser un fichier",
    textMode: "Texte inline",

    uploadTitle: "Téléverser un contenu à expliquer",
    allowedFileInputs:
      "Autorisés : .pdf et .docx. Rejetés automatiquement : .png, .jpg et les formats non pris en charge.",
    outputExtensionWillBe: "L’extension de sortie sera",

    pasteTextLabel: "Coller le texte à expliquer",
    pasteTextPlaceholder: "Collez ou saisissez votre texte ici...",
    inlineTextTreatedAs:
      "Le texte inline est traité comme .txt, donc l’extension de sortie sera également .txt.",

    unsupportedFileType:
      "Type de fichier non pris en charge : {ext}. Seuls les fichiers .pdf et .docx sont autorisés. Les formats PNG et autres images sont rejetés.",
    fileTooLarge:
      "Le fichier est trop volumineux. La taille maximale autorisée est de {maxSize} Mo.",
    explanationFailed:
      "Une erreur s’est produite lors de la génération de l’explication.",

    generatingExplanation: "Génération de l’explication...",
    outputFormatLabel: "Format de sortie :",

    policyTitle: "Règles de format",
    policySubtitle: "Correspondance stricte entre entrée et sortie",
    allowedUploadsLabel: "Téléversements autorisés :",
    inlineInputLabel: "Entrée inline :",
    rejectedAutomaticallyLabel: "Rejetés automatiquement :",
    outputRuleLabel: "Règle de sortie :",
    inlineInputValue: "traité comme .txt",
    rejectedAutomaticallyValue:
      ".png et tous les types de fichiers non pris en charge",
    outputRuleValue:
      "l’extension de sortie doit toujours être identique à l’extension d’entrée",

    explanationOutputTitle: "Résultat de l’explication",
    previewEmpty:
      "Votre explication générée apparaîtra ici. L’extension de sortie reflète toujours l’extension d’entrée d’origine.",
    outputExtensionLabel: "Extension de sortie :",

    inlineExplanationIntro: "Explication générée à partir du texte inline.",
    fileExplanationIntro: "Explication générée à partir de {filename}.",
    rewrittenPreviewText:
      "Ce contenu a été reformulé en une explication plus simple tout en conservant la même règle de format de sortie.",
    previewLabel: "Aperçu :",
    inputExtensionLabel: "Extension d’entrée :",
    outputExtensionResultLabel: "Extension de sortie :",
    preservedExtensionMessage:
      "Le résultat de l’explication conserve la même extension que le fichier téléversé d’origine.",
  },
};
export const summarizePageTranslations = {
  en: {
    badge: "Summarize content",
    title: "Summarize documents or text with strict format rules",
    description:
      "Upload a PDF or Word document, or paste inline text. Unsupported files like PNG are automatically rejected, and the output extension always matches the input extension.",

    fileMode: "Upload file",
    textMode: "Inline text",

    uploadTitle: "Upload a supported document",
    allowedFileInputs:
      "Allowed: .pdf and .docx. Rejected automatically: .png, .jpg, and all unsupported formats.",
    outputExtensionWillBe: "Output extension will be",

    pasteTextLabel: "Paste text to summarize",
    pasteTextPlaceholder: "Paste or type your text here...",
    inlineTextTreatedAs:
      "Inline text is treated as .txt, so the output extension will also be .txt.",

    unsupportedFileType:
      "Unsupported file type: {ext}. Only .pdf and .docx uploads are allowed. PNG and other image formats are rejected.",
    fileTooLarge: "File is too large. Maximum allowed size is {maxSize} MB.",
    summaryFailed: "Something went wrong while generating the summary.",

    generatingSummary: "Generating summary...",

    policySubtitle: "Strict input and output matching",
    allowedUploadsLabel: "Allowed uploads:",
    inlineInputLabel: "Inline input:",
    rejectedAutomaticallyLabel: "Rejected automatically:",
    outputRuleLabel: "Output rule:",
    inlineInputValue: "treated as .txt",
    rejectedAutomaticallyValue: ".png and all unsupported file types",
    outputRuleValue: "output extension must always equal input extension",

    summaryOutputTitle: "Summary output",
    previewEmpty:
      "Your generated summary will appear here. The output extension will always mirror the original input extension.",
    outputExtensionLabel: "Output extension:",

    inlineSummaryIntro: "Summary generated from inline text.",
    fileSummaryIntro: "Summary generated from {filename}.",
    translatedPreviewLabel: "Preview:",
    inputExtensionLabel: "Input extension:",
    outputExtensionResultLabel: "Output extension:",
    preservedExtensionMessage:
      "The output format remains the same as the uploaded file format.",
  },
  fr: {
    badge: "Résumer le contenu",
    title:
      "Résumez des documents ou du texte avec des règles de format strictes",
    description:
      "Téléversez un PDF ou un document Word, ou collez du texte inline. Les fichiers non pris en charge comme PNG sont automatiquement rejetés, et l’extension de sortie correspond toujours à l’extension d’entrée.",

    fileMode: "Téléverser un fichier",
    textMode: "Texte inline",

    uploadTitle: "Téléverser un document pris en charge",
    allowedFileInputs:
      "Autorisés : .pdf et .docx. Rejetés automatiquement : .png, .jpg et tous les formats non pris en charge.",
    outputExtensionWillBe: "L’extension de sortie sera",

    pasteTextLabel: "Coller le texte à résumer",
    pasteTextPlaceholder: "Collez ou saisissez votre texte ici...",
    inlineTextTreatedAs:
      "Le texte inline est traité comme .txt, donc l’extension de sortie sera également .txt.",

    unsupportedFileType:
      "Type de fichier non pris en charge : {ext}. Seuls les fichiers .pdf et .docx sont autorisés. Les formats PNG et autres images sont rejetés.",
    fileTooLarge:
      "Le fichier est trop volumineux. La taille maximale autorisée est de {maxSize} Mo.",
    summaryFailed: "Une erreur s’est produite lors de la génération du résumé.",

    generatingSummary: "Génération du résumé...",

    policySubtitle: "Correspondance stricte entre entrée et sortie",
    allowedUploadsLabel: "Téléversements autorisés :",
    inlineInputLabel: "Entrée inline :",
    rejectedAutomaticallyLabel: "Rejetés automatiquement :",
    outputRuleLabel: "Règle de sortie :",
    inlineInputValue: "traité comme .txt",
    rejectedAutomaticallyValue:
      ".png et tous les types de fichiers non pris en charge",
    outputRuleValue:
      "l’extension de sortie doit toujours être identique à l’extension d’entrée",

    summaryOutputTitle: "Résultat du résumé",
    previewEmpty:
      "Votre résumé généré apparaîtra ici. L’extension de sortie reflétera toujours l’extension d’entrée d’origine.",
    outputExtensionLabel: "Extension de sortie :",

    inlineSummaryIntro: "Résumé généré à partir du texte inline.",
    fileSummaryIntro: "Résumé généré à partir de {filename}.",
    translatedPreviewLabel: "Aperçu :",
    inputExtensionLabel: "Extension d’entrée :",
    outputExtensionResultLabel: "Extension de sortie :",
    preservedExtensionMessage:
      "Le format de sortie reste identique à celui du fichier téléversé.",
  },
};

export const translatePageTranslations = {
  en: {
    badge: "Translate content naturally",
    title: "Translate documents or text while preserving format rules",
    description:
      "Upload a PDF or Word document, or paste inline text. Unsupported files like PNG are rejected automatically, and the output extension always matches the input extension.",

    fileMode: "Upload file",
    textMode: "Inline text",

    targetLanguageLabel: "Translate to",
    targetLanguagePlaceholder:
      "Enter any target language, e.g. French, Yoruba, Japanese",
    targetLanguageHelp:
      "Enter any target language supported by the model instead of choosing from a limited dropdown.",

    uploadTitle: "Upload content to translate",
    allowedFileInputs:
      "Allowed: .pdf and .docx. Rejected automatically: .png, .jpg, and unsupported formats.",
    outputExtensionWillBe: "Output extension will be",

    pasteTextLabel: "Paste text to translate",
    pasteTextPlaceholder: "Paste or type your text here...",
    inlineTextTreatedAs:
      "Inline text is treated as .txt, so the output extension will also be .txt.",

    unsupportedFileType:
      "Unsupported file type: {ext}. Only .pdf and .docx uploads are allowed. PNG and other image formats are rejected.",
    fileTooLarge: "File is too large. Maximum allowed size is {maxSize} MB.",
    targetLanguageRequired: "Please enter a target language.",
    translationFailed: "Something went wrong while generating the translation.",

    generatingTranslation: "Generating translation...",

    policySubtitle: "Strict input and output matching",
    allowedUploadsLabel: "Allowed uploads:",
    inlineInputLabel: "Inline input:",
    rejectedAutomaticallyLabel: "Rejected automatically:",
    outputRuleLabel: "Output rule:",
    inlineInputValue: "treated as .txt",
    rejectedAutomaticallyValue: ".png and all unsupported file types",
    outputRuleValue: "output extension must always equal input extension",

    translationOutputTitle: "Translation output",
    previewEmpty:
      "Your generated translation will appear here. The output extension always mirrors the original input extension.",
    outputExtensionLabel: "Output extension:",

    inlineTranslationIntro: "Translation generated from inline text.",
    fileTranslationIntro: "Translation generated from {filename}.",
    targetLanguageResultLabel: "Target language:",
    translatedPreviewLabel: "Translated preview:",
    inputExtensionLabel: "Input extension:",
    outputExtensionResultLabel: "Output extension:",
    preservedExtensionMessage:
      "The translated output keeps the same extension as the uploaded input.",

    languageSuggestions: [
      "English",
      "French",
      "Spanish",
      "German",
      "Portuguese",
      "Brazilian Portuguese",
      "Arabic",
      "Chinese",
      "Simplified Chinese",
      "Traditional Chinese",
      "Japanese",
      "Korean",
      "Hindi",
      "Yoruba",
      "Hausa",
      "Igbo",
      "Swahili",
      "Turkish",
      "Russian",
      "Italian",
      "Dutch",
    ],
  },
  fr: {
    badge: "Traduire le contenu naturellement",
    title:
      "Traduisez des documents ou du texte tout en préservant les règles de format",
    description:
      "Téléversez un PDF ou un document Word, ou collez du texte inline. Les fichiers non pris en charge comme PNG sont automatiquement rejetés, et l’extension de sortie correspond toujours à l’extension d’entrée.",

    fileMode: "Téléverser un fichier",
    textMode: "Texte inline",

    targetLanguageLabel: "Traduire vers",
    targetLanguagePlaceholder:
      "Saisissez une langue cible, par ex. français, yoruba, japonais",
    targetLanguageHelp:
      "Saisissez toute langue cible prise en charge par le modèle au lieu de choisir dans une liste limitée.",

    uploadTitle: "Téléverser un contenu à traduire",
    allowedFileInputs:
      "Autorisés : .pdf et .docx. Rejetés automatiquement : .png, .jpg et les formats non pris en charge.",
    outputExtensionWillBe: "L’extension de sortie sera",

    pasteTextLabel: "Coller le texte à traduire",
    pasteTextPlaceholder: "Collez ou saisissez votre texte ici...",
    inlineTextTreatedAs:
      "Le texte inline est traité comme .txt, donc l’extension de sortie sera également .txt.",

    unsupportedFileType:
      "Type de fichier non pris en charge : {ext}. Seuls les fichiers .pdf et .docx sont autorisés. Les formats PNG et autres images sont rejetés.",
    fileTooLarge:
      "Le fichier est trop volumineux. La taille maximale autorisée est de {maxSize} Mo.",
    targetLanguageRequired: "Veuillez saisir une langue cible.",
    translationFailed:
      "Une erreur s’est produite lors de la génération de la traduction.",

    generatingTranslation: "Génération de la traduction...",

    policySubtitle: "Correspondance stricte entre entrée et sortie",
    allowedUploadsLabel: "Téléversements autorisés :",
    inlineInputLabel: "Entrée inline :",
    rejectedAutomaticallyLabel: "Rejetés automatiquement :",
    outputRuleLabel: "Règle de sortie :",
    inlineInputValue: "traité comme .txt",
    rejectedAutomaticallyValue:
      ".png et tous les types de fichiers non pris en charge",
    outputRuleValue:
      "l’extension de sortie doit toujours être identique à l’extension d’entrée",

    translationOutputTitle: "Résultat de la traduction",
    previewEmpty:
      "Votre traduction générée apparaîtra ici. L’extension de sortie reflète toujours l’extension d’entrée d’origine.",
    outputExtensionLabel: "Extension de sortie :",

    inlineTranslationIntro: "Traduction générée à partir du texte inline.",
    fileTranslationIntro: "Traduction générée à partir de {filename}.",
    targetLanguageResultLabel: "Langue cible :",
    translatedPreviewLabel: "Aperçu traduit :",
    inputExtensionLabel: "Extension d’entrée :",
    outputExtensionResultLabel: "Extension de sortie :",
    preservedExtensionMessage:
      "Le résultat traduit conserve la même extension que l’entrée téléversée.",

    languageSuggestions: [
      "English",
      "French",
      "Spanish",
      "German",
      "Portuguese",
      "Brazilian Portuguese",
      "Arabic",
      "Chinese",
      "Simplified Chinese",
      "Traditional Chinese",
      "Japanese",
      "Korean",
      "Hindi",
      "Yoruba",
      "Hausa",
      "Igbo",
      "Swahili",
      "Turkish",
      "Russian",
      "Italian",
      "Dutch",
    ],
  },
};
export const transcribePageTranslations = {
  en: {
    badge: "Transcription",
    title: "Transcribe audio and video",
    description: "Upload audio (.mp3) or video (.mp4, .mkv, .mov)",
    uploadTitle: "Upload audio or video",
    allowedFileInputs: "Allowed inputs: .mp3, .mp4, .mkv, .mov",
    unsupportedFileType: "Unsupported file type: {ext}",
    fileTooLarge:
      "File is too large, maximum size for this media type is {maxSize} MB",
    mediaTooLong:
      "Media is too long, maximum duration for this media type is {maxDuration}",
    couldNotReadDuration:
      "Could not read media duration, Please try another file",
    chooseFileToTranscribe: "Please choose an audio or video file",
    transcriptionFailed: "Transcription request failed",
    validatingMedia: "Checking media",
    transcriptOutput: "Transcript output",
    previewText: "Your transcript will appear here after processing",

    transcriptOptionsTitle: "Transcription options",
    transcriptOptionsSubtitle: "Choose how the transcript should be processed",
    preserveFillerWordsLabel: "Preserve filler words",
    preserveFillerWordsHelp: "Keep words like “um”, “uh”, and similar fillers",
    removeBackgroundNoiseLabel: "Remove background noise",
    removeBackgroundNoiseHelp:
      "Apply optional minimal background-noise cleanup",
    diarizeSpeakersLabel: "Separate speakers",
    diarizeSpeakersHelp:
      "Separate speakers only when they are acoustically detectable",

    transcriptReady: "Transcript ready",
    transcriptReadyText:
      "The spoken content has been converted into written text",
    transcriptMetaLabel: "Transcript output",
    transcriptMetaValue: "Inline text with .pdf download",
    downloadPdfTranscript: "Download PDF transcript",
    detectedTypeLabel: "Detected media type",
    durationLabel: "Duration",
    audioType: "Audio",
    videoType: "Video",
    unknownType: "Unknown",
  },
  fr: {
    badge: "Transcription",
    title: "Transcrire l’audio et la vidéo",
    description:
      "Téléversez un fichier audio (.mp3) ou vidéo (.mp4, .mkv, .mov)",
    uploadTitle: "Téléverser un fichier audio ou vidéo",
    allowedFileInputs: "Entrées autorisées: .mp3, .mp4, .mkv, .mov",
    unsupportedFileType: "Type de fichier non pris en charge: {ext}",
    fileTooLarge:
      "Le fichier est trop volumineux, la taille maximale pour ce type de média est de {maxSize} Mo",
    mediaTooLong:
      "Le média est trop long, la durée maximale pour ce type de média est de {maxDuration}",
    couldNotReadDuration:
      "Impossible de lire la durée du média, veuillez essayer un autre fichier",
    chooseFileToTranscribe: "Veuillez choisir un fichier audio ou vidéo",
    transcriptionFailed: "La requête de transcription a échoué",
    validatingMedia: "Vérification du média",
    transcriptOutput: "Résultat de la transcription",
    previewText: "Votre transcription apparaîtra ici après le traitement",

    transcriptOptionsTitle: "Options de transcription",
    transcriptOptionsSubtitle:
      "Choisissez comment la transcription doit être traitée",
    preserveFillerWordsLabel: "Préserver les mots de remplissage",
    preserveFillerWordsHelp:
      "Conserver les mots comme « euh », « hum » et équivalents",
    removeBackgroundNoiseLabel: "Réduire le bruit de fond",
    removeBackgroundNoiseHelp:
      "Appliquer un nettoyage minimal et optionnel du bruit de fond",
    diarizeSpeakersLabel: "Séparer les intervenants",
    diarizeSpeakersHelp:
      "Séparer les intervenants uniquement lorsqu’ils sont détectables acoustiquement",

    transcriptReady: "Transcription prête",
    transcriptReadyText: "Le contenu parlé a été converti en texte écrit",
    transcriptMetaLabel: "Sortie de transcription",
    transcriptMetaValue: "Aperçu texte inline avec téléchargement PDF",
    downloadPdfTranscript: "Télécharger la transcription PDF",
    detectedTypeLabel: "Type de média détecté",
    durationLabel: "Durée",
    audioType: "Audio",
    videoType: "Vidéo",
    unknownType: "Inconnu",
  },
};
export const redactPageTranslations = {
  en: {
    badge: "Privacy-first black-box redaction",
    title: "Redact sensitive data and information",
    description:
      "Upload files or documents to redact sensitive data and information keeping its structure",
    uploadTitle: "Upload file or document",
    allowedFileInputs: "Allowed: .pdf, .docx, .jpg, .jpeg, .png.",
    outputExtensionWillBe: "Output extension will be",
    unsupportedFileType:
      "Unsupported file type: {ext}. Only .pdf, .docx, .jpg, .jpeg, and .png are allowed.",
    fileTooLarge: "File is too large. Maximum allowed size is {maxSize} MB.",
    chooseFileToRedact: "Please choose a file to redact.",
    redactionFailed: "Something went wrong while processing redaction.",
    redactAction: "Redact document",
    generating: "Redacting...",
    reviewing: "Processing review...",
    finalizing: "Finalizing...",
    processAndReview: "Process and review",
    finalizeAction: "Generate final file",
    resultTitle: "Redaction output",
    previewEmpty:
      "Your provisional or final redacted file will appear here together with grouped review items.",
    policySubtitle: "Strict privacy processing rules",
    allowedUploadsLabel: "Allowed uploads:",
    outputRuleLabel: "Output rule:",
    outputRuleValue: "output extension must always equal input extension",
    docTypeLabel: "Document type",
    sensitiveTargetsLabel: "Sensitive data to redact",
    exclusionsLabel: "Type characters to redact",
    exclusionsPlaceholder:
      "Optional: enter exact words, names, phrases, or characters to redact, one per line or comma-separated.",
    selectedTargetsLabel: "Selected targets:",
    fileAcceptedLabel: "File accepted",
    downloadReady: "Download ready",
    outputReadyText: "Your final redacted file is ready to download.",
    missingDownloadUrl:
      "Processing finished, but the backend did not return a download URL.",
    processedFile: "Processed file",
    inputFile: "Input file",
    inputExtension: "Input extension",
    outputExtension: "Output extension",
    documentTypeResult: "Document type",
    exclusionsCount: "Items left visible count",
    rulesApplied: "Redaction was generated from the reviewed selection set.",
    fileTypeLabel: "Detected type:",
    selectAll: "Select all",
    clearAll: "Clear all",

    provisionalReady: "Provisional redacted file ready",
    finalReady: "Final redacted file ready",
    reviewTitle: "Review grouped redaction items",
    reviewHint:
      "Checked items stay redacted everywhere they appear. Unchecked items stay visible everywhere they appear.",
    reviewItemsLabel: "Grouped review items",
    processedPreviewTitle: "Processed document preview",
    docxPreviewNotice:
      "DOCX preview is shown using a generated PDF preview for review. Final download remains DOCX.",
    approveAll: "Approve all",
    clearApproved: "Clear all",
    approvedCountLabel: "Approved items",
    deselectedCountLabel: "Deselected items",
    occurrencesLabel: "Occurrences",
    noCandidates:
      "No grouped sensitive items were detected for the current settings.",
  },
  fr: {
    badge: "Caviardage en boîte noire axé sur la confidentialité",
    title: "Caviarder les données et informations sensibles",
    description:
      "Téléversez des fichiers ou des documents pour caviarder les données et informations sensibles tout en conservant leur structure",
    uploadTitle: "Téléverser un fichier ou un document",
    allowedFileInputs: "Autorisés : .pdf, .docx, .jpg, .jpeg, .png.",
    outputExtensionWillBe: "L’extension de sortie sera",
    unsupportedFileType:
      "Type de fichier non pris en charge : {ext}. Seuls .pdf, .docx, .jpg, .jpeg et .png sont autorisés.",
    fileTooLarge:
      "Le fichier est trop volumineux. La taille maximale autorisée est de {maxSize} MB.",
    chooseFileToRedact: "Veuillez choisir un fichier à caviarder.",
    redactionFailed:
      "Une erreur s’est produite pendant le traitement du caviardage.",
    redactAction: "Caviarder le document",
    generating: "Caviardage...",
    reviewing: "Préparation de la révision...",
    finalizing: "Finalisation...",
    processAndReview: "Traiter et réviser",
    finalizeAction: "Générer le fichier final",
    resultTitle: "Sortie du caviardage",
    previewEmpty:
      "Votre fichier caviardé provisoire ou final apparaîtra ici avec les éléments groupés à réviser.",
    policySubtitle: "Règles strictes de traitement confidentiel",
    allowedUploadsLabel: "Téléversements autorisés :",
    outputRuleLabel: "Règle de sortie :",
    outputRuleValue:
      "l’extension de sortie doit toujours être identique à l’extension d’entrée",
    docTypeLabel: "Type de document",
    sensitiveTargetsLabel: "Données sensibles à caviarder",
    exclusionsLabel: "Saisir les caractères à caviarder",
    exclusionsPlaceholder:
      "Optionnel : saisissez les mots, noms, expressions ou caractères exacts à caviarder, une entrée par ligne ou séparée par des virgules.",
    selectedTargetsLabel: "Cibles sélectionnées :",
    fileAcceptedLabel: "Fichier accepté",
    downloadReady: "Téléchargement prêt",
    outputReadyText: "Votre fichier caviardé final est prêt à être téléchargé.",
    missingDownloadUrl:
      "Le traitement est terminé, mais le backend n’a pas renvoyé d’URL de téléchargement.",
    processedFile: "Fichier traité",
    inputFile: "Fichier d’entrée",
    inputExtension: "Extension d’entrée",
    outputExtension: "Extension de sortie",
    documentTypeResult: "Type de document",
    exclusionsCount: "Nombre d’éléments laissés visibles",
    rulesApplied:
      "Le caviardage a été généré à partir de l’ensemble sélectionné après révision.",
    fileTypeLabel: "Type détecté :",
    selectAll: "Tout sélectionner",
    clearAll: "Tout effacer",

    provisionalReady: "Fichier caviardé provisoire prêt",
    finalReady: "Fichier caviardé final prêt",
    reviewTitle: "Réviser les éléments groupés à caviarder",
    reviewHint:
      "Les éléments cochés restent caviardés partout où ils apparaissent. Les éléments décochés restent visibles partout où ils apparaissent.",
    reviewItemsLabel: "Éléments groupés à réviser",
    processedPreviewTitle: "Aperçu du document traité",
    docxPreviewNotice:
      "L’aperçu DOCX est affiché à l’aide d’un aperçu PDF généré pour la révision. Le téléchargement final reste en DOCX.",
    approveAll: "Tout approuver",
    clearApproved: "Tout effacer",
    approvedCountLabel: "Éléments approuvés",
    deselectedCountLabel: "Éléments désélectionnés",
    occurrencesLabel: "Occurrences",
    noCandidates:
      "Aucun élément sensible groupé n’a été détecté pour les paramètres actuels.",
  },
};

export const dataMaskPageTranslations = {
  en: {
    badge: "Privacy-first black-box data masking",
    title: "Mask sensitive data and information",
    description:
      "Upload files or documents to mask sensitive data and information keeping its structure",
    uploadTitle: "Upload file or document",
    allowedFileInputs: "Allowed: .pdf, .docx, .jpg, .jpeg, .png.",
    outputExtensionWillBe: "Output extension will be",
    unsupportedFileType:
      "Unsupported file type: {ext}. Only .pdf, .docx, .jpg, .jpeg, and .png are allowed.",
    fileTooLarge: "File is too large. Maximum allowed size is {maxSize} MB.",
    chooseFileToMask: "Please choose a file to mask.",
    maskingFailed: "Something went wrong while processing data masking.",
    maskAction: "Mask document",
    generating: "Masking...",
    reviewing: "Processing review...",
    finalizing: "Finalizing...",
    processAndReview: "Process and review",
    finalizeAction: "Generate final file",
    resultTitle: "Data masking output",
    previewEmpty:
      "Your provisional or final masked file will appear here together with grouped review items.",
    policySubtitle: "Strict privacy processing rules",
    allowedUploadsLabel: "Allowed uploads:",
    outputRuleLabel: "Output rule:",
    outputRuleValue: "output extension must always equal input extension",
    docTypeLabel: "Document type",
    sensitiveTargetsLabel: "Sensitive data to mask",
    exclusionsLabel: "Type characters to mask",
    exclusionsPlaceholder:
      "Optional: enter words, characters, or numbers to mask, one per line or comma-separated.",
    selectedTargetsLabel: "Selected targets:",
    fileAcceptedLabel: "File accepted",
    downloadReady: "Download ready",
    outputReadyText: "Your final masked file is ready to download.",
    missingDownloadUrl:
      "Processing finished, but the backend did not return a download URL.",
    processedFile: "Processed file",
    inputFile: "Input file",
    inputExtension: "Input extension",
    outputExtension: "Output extension",
    documentTypeResult: "Document type",
    exclusionsCount: "Items left visible count",
    customMaskCount: "Custom mask items",
    rulesApplied: "Masking was generated from the reviewed selection set.",
    fileTypeLabel: "Detected type:",
    selectAll: "Select all",
    clearAll: "Clear all",

    provisionalReady: "Provisional masked file ready",
    finalReady: "Final masked file ready",
    reviewTitle: "Review grouped masking items",
    reviewHint:
      "Checked items stay masked everywhere they appear. Unchecked items stay visible everywhere they appear.",
    reviewItemsLabel: "Grouped review items",
    processedPreviewTitle: "Processed document preview",
    docxPreviewNotice:
      "DOCX preview is shown using a generated PDF preview for review. Final download remains DOCX.",
    approveAll: "Approve all",
    clearApproved: "Clear all",
    approvedCountLabel: "Approved items",
    deselectedCountLabel: "Deselected items",
    occurrencesLabel: "Occurrences",
    noCandidates:
      "No grouped sensitive items were detected for the current settings.",
  },
  fr: {
    badge: "Masquage en boîte noire axé sur la confidentialité",
    title: "Masquer les données et informations sensibles",
    description:
      "Téléversez des fichiers ou des documents pour masquer les données et informations sensibles tout en conservant leur structure",
    uploadTitle: "Téléverser un fichier ou un document",
    allowedFileInputs: "Autorisés : .pdf, .docx, .jpg, .jpeg, .png.",
    outputExtensionWillBe: "L’extension de sortie sera",
    unsupportedFileType:
      "Type de fichier non pris en charge : {ext}. Seuls .pdf, .docx, .jpg, .jpeg et .png sont autorisés.",
    fileTooLarge:
      "Le fichier est trop volumineux. La taille maximale autorisée est de {maxSize} MB.",
    chooseFileToMask: "Veuillez choisir un fichier à masquer.",
    maskingFailed:
      "Une erreur s’est produite pendant le traitement du masquage.",
    maskAction: "Masquer le document",
    generating: "Masquage...",
    reviewing: "Préparation de la révision...",
    finalizing: "Finalisation...",
    processAndReview: "Traiter et réviser",
    finalizeAction: "Générer le fichier final",
    resultTitle: "Sortie du masquage",
    previewEmpty:
      "Votre fichier masqué provisoire ou final apparaîtra ici avec les éléments groupés à réviser.",
    policySubtitle: "Règles strictes de traitement confidentiel",
    allowedUploadsLabel: "Téléversements autorisés :",
    outputRuleLabel: "Règle de sortie :",
    outputRuleValue:
      "l’extension de sortie doit toujours être identique à l’extension d’entrée",
    docTypeLabel: "Type de document",
    sensitiveTargetsLabel: "Données sensibles à masquer",
    exclusionsLabel: "Saisir les caractères à masquer",
    exclusionsPlaceholder:
      "Optionnel : saisissez les mots, caractères ou nombres à masquer, une par ligne ou séparés par des virgules.",
    selectedTargetsLabel: "Cibles sélectionnées :",
    fileAcceptedLabel: "Fichier accepté",
    downloadReady: "Téléchargement prêt",
    outputReadyText: "Votre fichier masqué final est prêt à être téléchargé.",
    missingDownloadUrl:
      "Le traitement est terminé, mais le backend n’a pas renvoyé d’URL de téléchargement.",
    processedFile: "Fichier traité",
    inputFile: "Fichier d’entrée",
    inputExtension: "Extension d’entrée",
    outputExtension: "Extension de sortie",
    documentTypeResult: "Type de document",
    exclusionsCount: "Nombre d’éléments laissés visibles",
    customMaskCount: "Éléments personnalisés à masquer",
    rulesApplied:
      "Le masquage a été généré à partir de l’ensemble sélectionné après révision.",
    fileTypeLabel: "Type détecté :",
    selectAll: "Tout sélectionner",
    clearAll: "Tout effacer",

    provisionalReady: "Fichier masqué provisoire prêt",
    finalReady: "Fichier masqué final prêt",
    reviewTitle: "Réviser les éléments groupés à masquer",
    reviewHint:
      "Les éléments cochés restent masqués partout où ils apparaissent. Les éléments décochés restent visibles partout où ils apparaissent.",
    reviewItemsLabel: "Éléments groupés à réviser",
    processedPreviewTitle: "Aperçu du document traité",
    docxPreviewNotice:
      "L’aperçu DOCX est affiché à l’aide d’un aperçu PDF généré pour la révision. Le téléchargement final reste en DOCX.",
    approveAll: "Tout approuver",
    clearApproved: "Tout effacer",
    approvedCountLabel: "Éléments approuvés",
    deselectedCountLabel: "Éléments désélectionnés",
    occurrencesLabel: "Occurrences",
    noCandidates:
      "Aucun élément sensible groupé n’a été détecté pour les paramètres actuels.",
  },
};
export const structuredExtractionPageTranslations = {
  en: {
    badge: "Structured document extraction",
    title: "Extract structured data from documents",
    description:
      "Upload files or document and export extracted fields, tables, and records",
    uploadTitle: "Upload document to extract",
    allowedFileInputs: "Allowed inputs: .pdf, .docx, .jpg, .jpeg, .png",
    extractionOutput: "Extraction result",
    previewText:
      "Your structured extraction file will appear here after processing",
    extractAction: "Extract data",
    extracting: "Extracting",
    extractionLabel: "Extraction:",
    extractionCompleted: "Structured extraction completed",

    unsupportedFileType:
      "Unsupported file type: {ext}. Only .pdf, .docx, .jpg, .jpeg, and .png are allowed",
    fileTooLarge: "File is too large, maximum allowed size is {maxSize} MB",
    chooseFileToExtract: "Please choose a file to extract from",
    documentClassRequired: "Select at least one document type",
    extractionFailed: "Something went wrong while extracting structured data",
    missingDownloadUrl:
      "Extraction finished, but the backend did not return a download URL",

    detectedType: "Detected type:",
    outputFormatLabel: "Download format",
    outputFormatHelp: "Choose the file format you want to download",
    outputFormatExamples:
      "Examples: JSON for apps, CSV for spreadsheets, Excel for review workbooks",
    resultShapeLabel: "Output layout",
    resultShapeHelp: "Choose how the extracted data should be organized",
    resultShapeExamples: "Not sure? Use Complete structured output",
    documentClassesLabel: "Document types",
    documentClassesHelp:
      "Select the document type that best matches your file, select more than one only if the file combines document types",
    documentClassesEmptyHelp: "At least one document type is required",
    documentClassesExamples:
      "Examples: Invoice, Bank statement, Contract, KYC document",
    searchDocumentClassesPlaceholder: "Search document types",
    selectedFieldsLabel: "Fields to extract",
    selectedFieldsHelp:
      "Optional, leave empty to extract all detected fields and add fields only when you know exactly what you need",
    selectedFieldsExamples: "Examples: invoice_number, invoice_date, total",
    selectedFieldsPlaceholder:
      "Optional, enter fields to extract and leave empty to extract detected fields",
    suggestedFieldsLabel: "Suggested fields",
    suggestedFieldsHelp:
      "Click common fields for the selected document type, suggestions are intentionally short to keep the page simple",
    suggestedFieldsExamples: "You can still type any custom field above",
    clearFields: "Clear fields",

    inputFile: "Input file",
    inputExtension: "Input extension",
    documentClassesResult: "Document types",
    resultShapeResult: "Output layout",
    outputFormatResult: "Download format",
    selectedFieldsResult: "Fields to extract",
    extractedFile: "Extracted file",
    allDetectedFields: "All detected fields",
    outputReadyText: "Your structured extraction file is ready to download",
    humanReviewRequired:
      "Human review is required before relying on or exporting the extracted data",
    downloadReady: "Download ready",

    outputFormatsTitle: "Outputs",
    reviewTitle: "Review",
    reviewValue: "Required",
    knowledgeTitle: "Knowledge",
    knowledgeValue: "Source-only",

    pdfDocument: "PDF document",
    wordDocument: "Word document",
    jpgImage: "JPG image",
    jpegImage: "JPEG image",
    pngImage: "PNG image",
    unknownFile: "Unknown file",

    outputFormatLabels: {
      json: ".json",
      csv: ".csv",
      xlsx: ".xlsx",
    },

    resultShapeLabels: {
      machine_readable: "Complete structured output (recommended)",
      key_value_fields: "Simple field/Value list",
      tables: "Extract tables only",
      row_based_records: "Spreadsheet/Database rows",
    },

    resultShapeDescriptions: {
      machine_readable:
        "Default includes fields, tables, records, warnings, and evidence in a complete structured file",
      key_value_fields:
        "Best for forms, invoices, receipts, IDs, HR records, and other documents with named fields",
      tables:
        "Best when the document contains visible tables and you mainly want table rows",
      row_based_records:
        "Best for repeated items such as transactions, line items, clauses, tickets, or database-ready rows",
    },

    documentClassLabels: {
      form: "Form",
      memo: "Memo",
      invoice: "Invoice",
      receipt: "Receipt",
      bank_statement: "Bank statement",
      kyc_document: "KYC document",
      id_document: "ID document",
      contract: "Contract",
      legal_record: "Legal record",
      medical_record: "Medical record",
      procurement_document: "Procurement document",
      technical_report: "Technical report",
      incident_report: "Incident report",
      insurance_document: "Insurance document",
      hr_record: "HR record",
      onboarding_document: "Onboarding document",
      ticket: "Ticket",
    },
  },

  fr: {
    badge: "Extraction structurée de documents",
    title: "Extraire des données structurées de documents",
    description:
      "Téléversez des fichiers ou documents et exportez les champs, tableaux et enregistrements extraits",
    uploadTitle: "Téléverser un document à extraire",
    allowedFileInputs: "Entrées autorisées: .pdf, .docx, .jpg, .jpeg, .png",
    extractionOutput: "Résultat de l’extraction",
    previewText:
      "Votre fichier d’extraction structurée apparaîtra ici après le traitement",
    extractAction: "Extraire les données",
    extracting: "Extraction",
    extractionLabel: "Extraction:",
    extractionCompleted: "Extraction structurée terminée",

    unsupportedFileType:
      "Type de fichier non pris en charge: {ext}. Seuls les formats .pdf, .docx, .jpg, .jpeg et .png sont autorisés",
    fileTooLarge:
      "Le fichier est trop volumineux, la taille maximale autorisée est de {maxSize} Mo",
    chooseFileToExtract: "Veuillez choisir un fichier à extraire",
    documentClassRequired: "Sélectionnez au moins un type de document",
    extractionFailed:
      "Une erreur s’est produite lors de l’extraction des données structurées",
    missingDownloadUrl:
      "L’extraction est terminée, mais le backend n’a pas renvoyé d’URL de téléchargement",

    detectedType: "Type détecté:",
    outputFormatLabel: "Format du téléchargement",
    outputFormatHelp: "Choisissez le format du fichier à télécharger",
    outputFormatExamples:
      "Exemples : JSON pour les applications, CSV pour les feuilles de calcul, Excel pour les classeurs de révision",
    resultShapeLabel: "Organisation de la sortie",
    resultShapeHelp:
      "Choisissez comment les données extraites doivent être organisées",
    resultShapeExamples:
      "Vous hésitez ? Utilisez la sortie structurée complète",
    documentClassesLabel: "Types de document",
    documentClassesHelp:
      "Sélectionnez le type qui correspond le mieux au fichier, sélectionnez plusieurs types seulement si le fichier combine réellement plusieurs documents",
    documentClassesEmptyHelp: "Au moins un type de document est requis",
    documentClassesExamples:
      "Exemples: Facture, Relevé bancaire, Contrat, Document KYC",
    searchDocumentClassesPlaceholder: "Rechercher des types de document",
    selectedFieldsLabel: "Champs à extraire",
    selectedFieldsHelp:
      "Facultatif, laisser vide pour extraire tous les champs détectés et n'ajouter des champs que lorsque vous savez exactement ce dont vous avez besoin",
    selectedFieldsExamples: "Exemples: invoice_number, invoice_date, total",
    selectedFieldsPlaceholder:
      "Facultatif, saisissez les champs à extraire ou laissez vide pour extraire les champs détectés",
    suggestedFieldsLabel: "Champs suggérés",
    suggestedFieldsHelp:
      "Cliquez sur les champs courants pour le type de document sélectionné, les suggestions sont volontairement courtes pour garder la page simple",
    suggestedFieldsExamples:
      "Vous pouvez toujours saisir un champ personnalisé ci-dessus",
    clearFields: "Effacer les champs",

    inputFile: "Fichier d’entrée",
    inputExtension: "Extension d’entrée",
    documentClassesResult: "Types de document",
    resultShapeResult: "Organisation de la sortie",
    outputFormatResult: "Format du téléchargement",
    selectedFieldsResult: "Champs à extraire",
    extractedFile: "Fichier extrait",
    allDetectedFields: "Tous les champs détectés",
    outputReadyText:
      "Votre fichier d’extraction structurée est prêt à être téléchargé",
    humanReviewRequired:
      "Une révision humaine est requise avant de se fier aux données extraites ou de les exporter",
    downloadReady: "Téléchargement prêt",

    outputFormatsTitle: "Sorties",
    reviewTitle: "Révision",
    reviewValue: "Requise",
    knowledgeTitle: "Connaissance",
    knowledgeValue: "Source uniquement",

    pdfDocument: "Document PDF",
    wordDocument: "Document Word",
    jpgImage: "Image JPG",
    jpegImage: "Image JPEG",
    pngImage: "Image PNG",
    unknownFile: "Fichier inconnu",

    outputFormatLabels: {
      json: ".json",
      csv: ".csv",
      xlsx: ".xlsx",
    },

    resultShapeLabels: {
      machine_readable: "Sortie structurée complète (recommandé)",
      key_value_fields: "Liste simple champ/Valeur",
      tables: "Extraire uniquement les tableaux",
      row_based_records: "Lignes pour tableur/Base de données",
    },

    resultShapeDescriptions: {
      machine_readable:
        "Par défaut, les fichiers structurés complets comprennent les champs, les tableaux, les enregistrements, les avertissements et les preuves",
      key_value_fields:
        "Idéal pour les formulaires, factures, reçus, pièces d’identité, dossiers RH et documents avec champs nommés",
      tables:
        "Idéal lorsque le document contient des tableaux visibles et que vous voulez surtout les lignes du tableau",
      row_based_records:
        "Idéal pour les éléments répétés comme transactions, lignes de facture, clauses, tickets ou lignes prêtes pour une base de données",
    },

    documentClassLabels: {
      form: "Formulaire",
      memo: "Note",
      invoice: "Facture",
      receipt: "Reçu",
      bank_statement: "Relevé bancaire",
      kyc_document: "Document KYC",
      id_document: "Pièce d’identité",
      contract: "Contrat",
      legal_record: "Dossier juridique",
      medical_record: "Dossier médical",
      procurement_document: "Document d’approvisionnement",
      technical_report: "Rapport technique",
      incident_report: "Rapport d’incident",
      insurance_document: "Document d’assurance",
      hr_record: "Dossier RH",
      onboarding_document: "Document d’intégration",
      ticket: "Ticket",
    },
  },
};
export const compliancePageTranslations = {
  en: {
    badge: "Multi-country compliance checks",
    title: "Check documents against compliance rules",
    description:
      "Upload files or documents and generate a compliance report from configured jurisdiction and sector rule packs",
    uploadTitle: "Upload document to check",
    allowedFileInputs: "Allowed inputs: .pdf, .docx, .jpg, .jpeg, .png",
    complianceOutput: "Compliance result",
    previewText:
      "Your compliance report will appear here after the document is checked",
    checkAction: "Check compliance",
    checking: "Checking",
    complianceLabel: "Compliance:",
    complianceCompleted: "Compliance check completed",

    unsupportedFileType:
      "Unsupported file type: {ext}. Only .pdf, .docx, .jpg, .jpeg, and .png are allowed",
    fileTooLarge: "File is too large, maximum allowed size is {maxSize} MB",
    chooseFileToCheck: "Please choose a file to check",
    complianceFailed: "Something went wrong while checking compliance",
    corePackRequired:
      "The core control library for {country} must be included with every sector-specific compliance check",
    missingDownloadUrl:
      "Compliance check finished but the backend did not return a download URL",

    detectedType: "Detected type:",
    jurisdictionLabel: "Country/Jurisdiction",
    jurisdictionHelp:
      "Choose the country whose compliance rules should be used",
    jurisdictionExamples:
      "Examples: Nigeria, South Africa, United States, United Kingdom",
    reportVariantLabel: "Report format",
    reportVariantHelp: "Choose how the compliance result should be delivered",
    reportVariantExamples: "Not sure? Use PDF report for review",
    sectorPacksLabel: "Business sector/Rule packs",
    corePackHelp:
      "The core control library for {country} is always included, add sector-specific packs when needed",
    sectorPacksEmptyHelp:
      "Choose the sector that best matches the document or business context",
    sectorPacksExamples:
      "Examples: Banking and fintech, Health, Insurance, Telecom",
    searchSectorPacksPlaceholder: "Search sectors",
    clearSectorPacks: "Clear sectors",
    requiredLabel: "required",
    regulatoryDomainsLabel: "Focus areas",
    regulatoryDomainsHelp:
      "Optional, select focus areas only when you want a narrower review",
    regulatoryDomainsEmptyHelp:
      "Nothing selected means check all available domains in the selected rule packs",
    regulatoryDomainsExamples:
      "Examples: Privacy for personal data, AML for KYC/Fintech, Licensing for regulated businesses",
    searchRegulatoryDomainsPlaceholder: "Search focus areas",
    clearDomains: "Clear focus areas",

    inputFile: "Input file",
    inputExtension: "Input extension",
    jurisdictionResult: "Country/jurisdiction",
    sectorPacksResult: "Sector packs",
    regulatoryDomainsResult: "Focus areas",
    reportVariantResult: "Report format",
    outputFormatResult: "Output format",
    reportFile: "Report file",
    allDomains: "All available focus areas",
    outputReadyText: "Your compliance report is ready to download",
    humanReviewRequired:
      "Human review is required before relying on or exporting the compliance result",
    downloadReady: "Download ready",

    findingsSummary: "Findings summary",
    passed: "Passed",
    failed: "Failed",
    warning: "Warning",
    missing: "Missing",
    reviewRequiredCount: "Review required",
    reviewRequiredShort: "Review",

    outputTitle: "Output",
    reviewTitle: "Review",
    reviewValue: "Required",
    scopeTitle: "Scope",
    scopeValue: "Expandable by jurisdiction",

    pdfDocument: "PDF document",
    wordDocument: "Word document",
    jpgImage: "JPG image",
    jpegImage: "JPEG image",
    pngImage: "PNG image",
    unknownFile: "Unknown file",

    countryLabels: {
      nigeria: "Nigeria",
      unitedStates: "United States",
      unitedKingdom: "United Kingdom",
      southAfrica: "South Africa",
      canada: "Canada",
      france: "France",
      togo: "Togo",
      ghana: "Ghana",
    },

    outputFormatLabels: {
      pdf: ".pdf",
      json: ".json",
    },

    reportVariantLabels: {
      human_readable_report: "PDF report for review (recommended)",
      machine_readable_report: "JSON report for systems/API",
      annotated_source_output: "Evidence-marked PDF",
    },

    reportVariantDescriptions: {
      human_readable_report:
        "Best for reading, sharing, and downloading a normal compliance report",
      machine_readable_report:
        "Best for developers, dashboards, databases, APIs, or automated workflows",
      annotated_source_output:
        "Best when a reviewer needs to verify findings against the original source document",
    },

    sectorPackLabels: {
      nigeria_core_control_library: "Core control library",
      core_control_library: "Core control library",

      accounting: "Accounting",
      agriculture: "Agriculture",
      aviation: "Aviation",
      banking_and_fintech: "Banking and fintech",
      payment_platforms_and_services: "Payment platforms and services",
      energy_and_power: "Energy and power",
      health: "Health",
      insurance: "Insurance",
      legal_and_law: "Legal and law",
      law_and_legal: "Law and legal",
      manufacturing: "Manufacturing",
      maritime: "Maritime",
      maritime_and_shipping: "Maritime and shipping",
      media: "Media",
      mining: "Mining",
      ngo: "NGO",
      oil_and_gas: "Oil and gas",
      pharmaceuticals: "Pharmaceuticals",
      sports: "Sports",
      tech: "Technology",
      telecom: "Telecom",
    },

    regulatoryDomainLabels: {
      privacy: "Privacy/Personal data",
      cybersecurity: "Cybersecurity",
      aml: "AML/Financial crime",
      consumer_protection: "Consumer protection",
      public_sector_access_to_information:
        "Public-sector access to information",
      licensing: "Licensing/Permits",
      registration: "Registration",
      sector_regulator_requirements: "Sector regulator rules",
    },
  },

  fr: {
    badge: "Contrôles de conformité multi-pays",
    title: "Vérifier les documents selon les règles de conformité",
    description:
      "Téléversez des fichiers ou documents et générez un rapport de conformité à partir des packs de règles configurés par juridiction et par secteur",
    uploadTitle: "Téléverser un document à vérifier",
    allowedFileInputs: "Entrées autorisées: .pdf, .docx, .jpg, .jpeg, .png",
    complianceOutput: "Résultat de conformité",
    previewText:
      "Votre rapport de conformité apparaîtra ici après la vérification du document",
    checkAction: "Vérifier la conformité",
    checking: "Vérification",
    complianceLabel: "Conformité:",
    complianceCompleted: "Vérification de conformité terminée",

    unsupportedFileType:
      "Type de fichier non pris en charge: {ext}. Seuls les formats .pdf, .docx, .jpg, .jpeg et .png sont autorisés",
    fileTooLarge:
      "Le fichier est trop volumineux, la taille maximale autorisée est de {maxSize} Mo",
    chooseFileToCheck: "Veuillez choisir un fichier à vérifier",
    complianceFailed:
      "Une erreur s’est produite lors de la vérification de conformité",
    corePackRequired:
      "La bibliothèque de contrôles de base pour {country} doit être incluse avec chaque vérification sectorielle",
    missingDownloadUrl:
      "La vérification de conformité est terminée, mais le backend n’a pas renvoyé d’URL de téléchargement",

    detectedType: "Type détecté:",
    jurisdictionLabel: "Pays/Juridiction",
    jurisdictionHelp:
      "Choisissez le pays dont les règles de conformité doivent être utilisées",
    jurisdictionExamples:
      "Exemples: Nigeria, Afrique du Sud, États-Unis, Royaume-Uni",
    reportVariantLabel: "Format du rapport",
    reportVariantHelp:
      "Choisissez comment le résultat de conformité doit être livré",
    reportVariantExamples:
      "Vous hésitez? Utilisez le rapport PDF pour révision",
    sectorPacksLabel: "Secteur d’activité/Packs de règles",
    corePackHelp:
      "La bibliothèque de contrôles de base pour {country} est toujours incluse, ajoutez des packs sectoriels si nécessaire",
    sectorPacksEmptyHelp:
      "Choisissez le secteur qui correspond le mieux au document ou au contexte de l’entreprise",
    sectorPacksExamples:
      "Exemples : Banque et fintech, Santé, Assurance, Télécoms",
    searchSectorPacksPlaceholder: "Rechercher des secteurs",
    clearSectorPacks: "Effacer les secteurs",
    requiredLabel: "requis",
    regulatoryDomainsLabel: "Domaines de vérification",
    regulatoryDomainsHelp:
      "Optionnel, sélectionnez des domaines seulement si vous voulez une vérification plus ciblée",
    regulatoryDomainsEmptyHelp:
      "Rien n'est sélectionné signifie cocher tous les domaines disponibles dans les ensembles de règles sélectionnés",
    regulatoryDomainsExamples:
      "Exemples: Confidentialité pour les données personnelles, LBC pour KYC/Fintech, Licences pour les activités réglementées",
    searchRegulatoryDomainsPlaceholder: "Rechercher des domaines",
    clearDomains: "Effacer les domaines",

    inputFile: "Fichier d’entrée",
    inputExtension: "Extension d’entrée",
    jurisdictionResult: "Pays/Juridiction",
    sectorPacksResult: "Packs sectoriels",
    regulatoryDomainsResult: "Domaines de vérification",
    reportVariantResult: "Format du rapport",
    outputFormatResult: "Format de sortie",
    reportFile: "Fichier du rapport",
    allDomains: "Tous les domaines disponibles",
    outputReadyText: "Votre rapport de conformité est prêt à être téléchargé",
    humanReviewRequired:
      "Une révision humaine est requise avant de se fier au résultat de conformité ou de l’exporter",
    downloadReady: "Téléchargement prêt",

    findingsSummary: "Résumé des constats",
    passed: "Réussi",
    failed: "Échoué",
    warning: "Avertissement",
    missing: "Manquant",
    reviewRequiredCount: "Révision requise",
    reviewRequiredShort: "Révision",

    outputTitle: "Sortie",
    reviewTitle: "Révision",
    reviewValue: "Requise",
    scopeTitle: "Portée",
    scopeValue: "Extensible par juridiction",

    pdfDocument: "Document PDF",
    wordDocument: "Document Word",
    jpgImage: "Image JPG",
    jpegImage: "Image JPEG",
    pngImage: "Image PNG",
    unknownFile: "Fichier inconnu",

    countryLabels: {
      nigeria: "Nigeria",
      unitedStates: "États-Unis",
      unitedKingdom: "Royaume-Uni",
      southAfrica: "Afrique du Sud",
      canada: "Canada",
      france: "France",
      togo: "Togo",
      ghana: "Ghana",
    },

    outputFormatLabels: {
      pdf: ".pdf",
      json: ".json",
    },

    reportVariantLabels: {
      human_readable_report: "Rapport PDF pour révision (recommandé)",
      machine_readable_report: "Rapport JSON pour systèmes/API",
      annotated_source_output: "PDF avec preuves marquées",
    },

    reportVariantDescriptions: {
      human_readable_report:
        "Idéal pour lire, partager et télécharger un rapport de conformité normal",
      machine_readable_report:
        "Idéal pour les développeurs, tableaux de bord, bases de données, API ou workflows automatisés",
      annotated_source_output:
        "Idéal lorsqu’un réviseur doit vérifier les constats par rapport au document source original",
    },

    sectorPackLabels: {
      nigeria_core_control_library: "Bibliothèque de contrôles de base",
      core_control_library: "Bibliothèque de contrôles de base",

      accounting: "Comptabilité",
      agriculture: "Agriculture",
      aviation: "Aviation",
      banking_and_fintech: "Banque et fintech",
      payment_platforms_and_services: "Plateformes et services de paiement",
      energy_and_power: "Énergie et électricité",
      health: "Santé",
      insurance: "Assurance",
      legal_and_law: "Juridique et droit",
      law_and_legal: "Droit et juridique",
      manufacturing: "Fabrication",
      maritime: "Maritime",
      maritime_and_shipping: "Maritime et transport maritime",
      media: "Médias",
      mining: "Mines",
      ngo: "ONG",
      oil_and_gas: "Pétrole et gaz",
      pharmaceuticals: "Produits pharmaceutiques",
      sports: "Sports",
      tech: "Technologie",
      telecom: "Télécoms",
    },

    regulatoryDomainLabels: {
      privacy: "Confidentialité/Données personnelles",
      cybersecurity: "Cybersécurité",
      aml: "LBC/Criminalité financière",
      consumer_protection: "Protection des consommateurs",
      public_sector_access_to_information:
        "Accès à l’information du secteur public",
      licensing: "Licences/Permis",
      registration: "Enregistrement",
      sector_regulator_requirements: "Règles des régulateurs sectoriels",
    },
  },
};

export const teamPageTranslations = {
  en: {
    title: "Projects & Team",
    subtitle:
      "Manage your organization, subscription seats, members, and invitations.",
    loading: "Loading team...",
    noTeam: "No team workspace found.",
    refresh: "Refresh",
    organization: "Organization",
    plan: "Plan",
    seats: "Seats",
    seatsUsed: "Seats used",
    seatLimitReached: "Seat limit reached",
    upgradeRequired: "Upgrade required",
    upgradeRequiredDescription:
      "Your team has used all available seats. Remove a member or upgrade the subscription before inviting another member.",
    role: "Your role",
    members: "Members",
    invitations: "Pending invitations",
    invitationsForYou: "Invitations for you",
    invitationsForYouDescription:
      "Accept an invitation to join a Business or Enterprise workspace.",
    acceptInvitation: "Accept invitation",
    accepting: "Accepting...",
    acceptedInvitation: "Invitation accepted.",
    denyInvitation: "Deny",
    denying: "Denying...",
    deniedInvitation: "Invitation denied.",
    cancelInvitation: "Cancel invitation",
    cancellingInvitation: "Cancelling...",
    invitationCancelled: "Invitation cancelled.",
    leavePlan: "Leave plan",
    leavingPlan: "Leaving...",
    leftPlan: "You left the plan.",
    invite: "Invite member",
    email: "Email address",
    member: "Member",
    admin: "Admin",
    send: "Send invite",
    remove: "Remove",
    none: "None",
    ownerNote: "Owners cannot be removed here. Assign another owner first.",
  },
  fr: {
    title: "Projets & équipe",
    subtitle:
      "Gérez votre organisation, les sièges d’abonnement, les membres et les invitations.",
    loading: "Chargement de l’équipe...",
    noTeam: "Aucun espace d’équipe trouvé.",
    refresh: "Actualiser",
    organization: "Organisation",
    plan: "Forfait",
    seats: "Sièges",
    seatsUsed: "Sièges utilisés",
    seatLimitReached: "Limite de sièges atteinte",
    upgradeRequired: "Mise à niveau requise",
    upgradeRequiredDescription:
      "Votre équipe a utilisé tous les sièges disponibles. Retirez un membre ou augmentez l’abonnement avant d’inviter un autre membre.",
    role: "Votre rôle",
    members: "Membres",
    invitations: "Invitations en attente",
    invitationsForYou: "Invitations pour vous",
    invitationsForYouDescription:
      "Acceptez une invitation pour rejoindre un espace Business ou Enterprise.",
    acceptInvitation: "Accepter l’invitation",
    accepting: "Acceptation...",
    acceptedInvitation: "Invitation acceptée.",
    denyInvitation: "Refuser",
    denying: "Refus...",
    deniedInvitation: "Invitation refusée.",
    cancelInvitation: "Annuler l’invitation",
    cancellingInvitation: "Annulation...",
    invitationCancelled: "Invitation annulée.",
    leavePlan: "Quitter le forfait",
    leavingPlan: "Départ...",
    leftPlan: "Vous avez quitté le forfait.",
    invite: "Inviter un membre",
    email: "Adresse e-mail",
    member: "Membre",
    admin: "Admin",
    send: "Envoyer l’invitation",
    remove: "Retirer",
    none: "Aucun",
    ownerNote:
      "Les propriétaires ne peuvent pas être retirés ici. Attribuez d’abord un autre propriétaire.",
  },
};


export const pdfToolsPageTranslations = {
  en: {
    back: "Back",
    badge: "PDF Tools",
    title: "Choose a PDF tool",
    description:
      "Authenticated free users can use PDF tools within the heavy-feature quota. Personal, Business, and Enterprise users have unlimited access through the paid-plan guards.",
    signInTitle: "Sign in required",
    signInDescription:
      "PDF tools are blocked for anonymous users. Sign in to use your free quota or paid-plan access.",
    signIn: "Sign in",
    loading: "Checking account...",
    freeQuota: "Authenticated free quota",
    paidUnlimited: "Paid plans: unlimited",
    quotaDescription:
      "Free accounts use the authenticated heavy-feature limits. Paid Personal, Business, and Enterprise accounts are validated without consuming usage buckets.",
    actions: [
      {
        key: "combinePdf",
        name: "Combine PDF",
        route: "/pdf-tools/combine",
        description: "Merge multiple PDFs in a controlled order.",
      },
      {
        key: "compressPdf",
        name: "Compress PDF",
        route: "/pdf-tools/compress",
        description: "Reduce PDF size while preserving visible content.",
      },
      {
        key: "editPdf",
        name: "Edit PDF",
        route: "/pdf-tools/edit",
        description: "Add text, highlights, drawings, images, whiteouts, and signatures.",
      },
      {
        key: "splitPdf",
        name: "Split PDF",
        route: "/pdf-tools/split",
        description: "Extract pages or ranges into separate PDFs.",
      },
    ],
  },
  fr: {
    back: "Retour",
    badge: "Outils PDF",
    title: "Choisissez un outil PDF",
    description:
      "Les utilisateurs gratuits authentifiés peuvent utiliser les outils PDF dans le quota des fonctionnalités lourdes. Les utilisateurs Personal, Business et Enterprise ont un accès illimité via les contrôles des forfaits payants.",
    signInTitle: "Connexion requise",
    signInDescription:
      "Les outils PDF sont bloqués pour les utilisateurs anonymes. Connectez-vous pour utiliser votre quota gratuit ou votre accès payant.",
    signIn: "Se connecter",
    loading: "Vérification du compte...",
    freeQuota: "Quota gratuit authentifié",
    paidUnlimited: "Forfaits payants : illimité",
    quotaDescription:
      "Les comptes gratuits utilisent les limites authentifiées des fonctionnalités lourdes. Les comptes payants Personal, Business et Enterprise sont validés sans consommer de quota d’usage.",
    actions: [
      {
        key: "combinePdf",
        name: "Combiner PDF",
        route: "/pdf-tools/combine",
        description: "Fusionnez plusieurs PDF dans un ordre contrôlé.",
      },
      {
        key: "compressPdf",
        name: "Compresser PDF",
        route: "/pdf-tools/compress",
        description: "Réduisez la taille du PDF tout en préservant le contenu visible.",
      },
      {
        key: "editPdf",
        name: "Modifier PDF",
        route: "/pdf-tools/edit",
        description: "Ajoutez texte, surlignages, dessins, images, masques blancs et signatures.",
      },
      {
        key: "splitPdf",
        name: "Diviser PDF",
        route: "/pdf-tools/split",
        description: "Extrayez des pages ou plages en PDF séparés.",
      },
    ],
  },
};

export const combinePdfPageTranslations = {
  en: { back: "Back", badge: "PDF tools", title: "Combine PDF files", description: "Merge up to 10 PDFs in the exact order shown. Existing typed, drawn, and uploaded-image signatures are preserved as PDF content.", uploadTitle: "PDF files", uploadHelp: "Allowed: .pdf only. Each file can be up to 50 MB.", chooseFiles: "Choose PDFs", outputFilename: "Output filename", preserveBookmarks: "Preserve bookmarks", preserveMetadata: "Preserve metadata", combine: "Combine PDFs", combining: "Combining...", resultTitle: "Combined PDF ready", download: "Download combined PDF", signInTitle: "Sign in required", signInDescription: "PDF tools use your authenticated analyzer quota.", signIn: "Sign in", loading: "Checking account...", noFiles: "Choose at least two PDF files.", invalidFile: "Only PDF files are supported.", tooMany: "You can combine at most 10 files.", tooLarge: "Each PDF must be 50 MB or smaller.", failed: "Could not combine PDFs." },
  fr: { back: "Retour", badge: "Outils PDF", title: "Combiner des PDF", description: "Fusionnez jusqu’à 10 PDF dans l’ordre affiché. Les signatures typées, dessinées et image déjà présentes sont préservées comme contenu PDF.", uploadTitle: "Fichiers PDF", uploadHelp: "Autorisé : .pdf uniquement. 50 Mo maximum par fichier.", chooseFiles: "Choisir des PDF", outputFilename: "Nom du fichier de sortie", preserveBookmarks: "Préserver les signets", preserveMetadata: "Préserver les métadonnées", combine: "Combiner", combining: "Combinaison...", resultTitle: "PDF combiné prêt", download: "Télécharger le PDF", signInTitle: "Connexion requise", signInDescription: "Les outils PDF utilisent votre quota authentifié.", signIn: "Se connecter", loading: "Vérification du compte...", noFiles: "Choisissez au moins deux PDF.", invalidFile: "Seuls les PDF sont pris en charge.", tooMany: "Vous pouvez combiner au plus 10 fichiers.", tooLarge: "Chaque PDF doit faire au plus 50 Mo.", failed: "Impossible de combiner les PDF." },
};

export const compressPdfPageTranslations = {
  en: { back: "Back", badge: "PDF tools", title: "Compress PDF files", description: "Compress PDFs while preserving visible PDF content, including existing typed, drawn, and uploaded-image signatures. New signature placement belongs in E-signature or Edit PDF.", uploadTitle: "Source PDF", uploadHelp: "Allowed: .pdf only. Maximum 50 MB.", chooseFile: "Choose PDF", compressionLevel: "Compression level", smallFile: "Small file", balanced: "Balanced", highQuality: "High quality", outputFilename: "Output filename", asyncProcessing: "Allow async processing", compress: "Compress PDF", compressing: "Compressing...", resultTitle: "Compressed PDF ready", download: "Download compressed PDF", signInTitle: "Sign in required", signInDescription: "PDF tools use your authenticated analyzer quota.", signIn: "Sign in", loading: "Checking account...", noFile: "Choose a PDF file.", invalidFile: "Only PDF files are supported.", tooLarge: "The PDF must be 50 MB or smaller.", failed: "Could not compress PDF." },
  fr: { back: "Retour", badge: "Outils PDF", title: "Compresser des PDF", description: "Compressez des PDF tout en préservant le contenu visible, y compris les signatures typées, dessinées et image déjà présentes. L’ajout de signatures se fait dans E-signature ou Modifier PDF.", uploadTitle: "PDF source", uploadHelp: "Autorisé : .pdf uniquement. Maximum 50 Mo.", chooseFile: "Choisir un PDF", compressionLevel: "Niveau de compression", smallFile: "Petit fichier", balanced: "Équilibré", highQuality: "Haute qualité", outputFilename: "Nom du fichier de sortie", asyncProcessing: "Autoriser le traitement asynchrone", compress: "Compresser", compressing: "Compression...", resultTitle: "PDF compressé prêt", download: "Télécharger", signInTitle: "Connexion requise", signInDescription: "Les outils PDF utilisent votre quota authentifié.", signIn: "Se connecter", loading: "Vérification du compte...", noFile: "Choisissez un PDF.", invalidFile: "Seuls les PDF sont pris en charge.", tooLarge: "Le PDF doit faire 50 Mo ou moins.", failed: "Impossible de compresser le PDF." },
};

export const editPdfPageTranslations = {
  en: {
    back: "Back",
    badge: "PDF tools",
    title: "Edit PDFs with text, drawing, image, and signature operations",
    description:
      "Build schema-aligned PDF edit operations, including typed, drawn, and uploaded-image signature operations.",
    uploadTitle: "Source PDF",
    uploadHelp: "Allowed: .pdf only. Maximum 50 MB.",
    chooseFile: "Choose PDF",
    operationBuilder: "Operation builder",
    operationType: "Operation type",
    addText: "Add text",
    removeText: "Remove text",
    addImage: "Add image",
    removeImage: "Remove image",
    draw: "Draw path",
    highlight: "Highlight",
    whiteout: "Whiteout",
    addSignature: "Add signature",
    removeSignature: "Remove signature",
    pageNumber: "Page number",
    rectangle: "Normalized rectangle",
    text: "Text",
    fontSize: "Font size",
    color: "Color",
    opacity: "Opacity",
    svgPath: "SVG path",
    strokeWidth: "Stroke width",
    imageStorageKey: "Image storage key",
    imageMimeType: "Image MIME type",
    signatureType: "Signature type",
    typed: "Typed",
    drawn: "Drawn",
    uploaded: "Uploaded image",
    typedName: "Typed signature name",
    signatureSvgStorageKey: "Signature SVG storage key",
    signatureImageStorageKey: "Signature image storage key",
    assetKeyHelp: "Drawn/uploaded signatures use storage keys because the backend resolves assets by key/path.",
    addOperation: "Add operation",
    operations: "Operations",
    noOperations: "No operations added yet.",
    outputFilename: "Output filename",
    generatePreview: "Generate preview PDF",
    edit: "Apply edits",
    editing: "Applying edits...",
    signInTitle: "Sign in required",
    signInDescription: "PDF tools use your authenticated analyzer quota.",
    signIn: "Sign in",
    loading: "Checking account...",
    resultTitle: "Edited PDF ready",
    applied: "Operations applied",
    requested: "Operations requested",
    download: "Download edited PDF",
    preview: "Download preview",
    noFile: "Choose a PDF file.",
    invalidFile: "Only PDF files are supported.",
    tooLarge: "The PDF must be 50 MB or smaller.",
    badRectangle: "Rectangle values must be between 0 and 1 and stay inside the page.",
    needsText: "This operation requires text.",
    needsPath: "Draw operation requires an SVG path.",
    needsImageKey: "Image operation requires an image storage key.",
    needsSignature: "Complete the selected signature source.",
    failed: "Could not edit PDF.",
  },
  fr: {
    back: "Retour",
    badge: "Outils PDF",
    title: "Modifier des PDF avec texte, dessin, image et signatures",
    description:
      "Construisez des opérations PDF conformes au schéma, y compris les signatures typées, dessinées et image.",
    uploadTitle: "PDF source",
    uploadHelp: "Autorisé : .pdf uniquement. Maximum 50 Mo.",
    chooseFile: "Choisir un PDF",
    operationBuilder: "Constructeur d’opération",
    operationType: "Type d’opération",
    addText: "Ajouter du texte",
    removeText: "Supprimer du texte",
    addImage: "Ajouter une image",
    removeImage: "Supprimer une image",
    draw: "Dessiner un chemin",
    highlight: "Surligner",
    whiteout: "Masquer en blanc",
    addSignature: "Ajouter une signature",
    removeSignature: "Supprimer une signature",
    pageNumber: "Page",
    rectangle: "Rectangle normalisé",
    text: "Texte",
    fontSize: "Taille police",
    color: "Couleur",
    opacity: "Opacité",
    svgPath: "Chemin SVG",
    strokeWidth: "Épaisseur",
    imageStorageKey: "Clé de stockage image",
    imageMimeType: "MIME image",
    signatureType: "Type de signature",
    typed: "Typée",
    drawn: "Dessinée",
    uploaded: "Image téléversée",
    typedName: "Nom de signature typée",
    signatureSvgStorageKey: "Clé SVG de signature",
    signatureImageStorageKey: "Clé image de signature",
    assetKeyHelp: "Les signatures dessinées/téléversées utilisent des clés car le backend résout les assets par clé/chemin.",
    addOperation: "Ajouter l’opération",
    operations: "Opérations",
    noOperations: "Aucune opération ajoutée.",
    outputFilename: "Nom du fichier de sortie",
    generatePreview: "Générer un aperçu PDF",
    edit: "Appliquer les modifications",
    editing: "Application...",
    signInTitle: "Connexion requise",
    signInDescription: "Les outils PDF utilisent votre quota authentifié.",
    signIn: "Se connecter",
    loading: "Vérification du compte...",
    resultTitle: "PDF modifié prêt",
    applied: "Opérations appliquées",
    requested: "Opérations demandées",
    download: "Télécharger le PDF modifié",
    preview: "Télécharger l’aperçu",
    noFile: "Choisissez un PDF.",
    invalidFile: "Seuls les PDF sont pris en charge.",
    tooLarge: "Le PDF doit faire 50 Mo ou moins.",
    badRectangle: "Le rectangle doit être entre 0 et 1 et rester dans la page.",
    needsText: "Cette opération nécessite du texte.",
    needsPath: "Le dessin nécessite un chemin SVG.",
    needsImageKey: "L’image nécessite une clé de stockage.",
    needsSignature: "Complétez la source de signature choisie.",
    failed: "Impossible de modifier le PDF.",
  },
};

export const esignaturePageTranslations = {
  en: {
    back: "Back",
    badge: "ReDOCX Sign",
    title: "E-sign PDFs with typed, drawn, or uploaded signatures",
    description:
      "Create a self-signing workflow or send a PDF for signature using the backend e-signature contract.",
    uploadTitle: "Source PDF",
    uploadHelp: "Allowed: .pdf only. Maximum 50 MB.",
    chooseFile: "Choose PDF",
    workflow: "Workflow",
    selfSign: "Self-sign now",
    sendSingle: "Send to one recipient",
    sendMultiple: "Send to multiple recipients",
    selfSignThenSend: "Self-sign, then send",
    routingMode: "Routing mode",
    sequential: "Sequential",
    parallel: "Parallel",
    signerDetails: "Owner / signer details",
    signerName: "Signer name",
    signerEmail: "Signer email",
    recipients: "Recipients",
    recipientName: "Recipient name",
    recipientEmail: "Recipient email",
    signingOrder: "Signing order",
    addRecipient: "Add recipient",
    fields: "Fields",
    addField: "Add field",
    fieldType: "Field type",
    assignedTo: "Assigned to",
    pageNumber: "Page",
    rectangle: "Normalized rectangle",
    x: "X",
    y: "Y",
    width: "Width",
    height: "Height",
    emailSubject: "Email subject",
    emailMessage: "Email message",
    expiresInDays: "Expires in days",
    sendEmails: "Send invitation emails",
    signatureSource: "Signature source",
    typed: "Typed",
    drawn: "Drawn",
    uploaded: "Uploaded image",
    typedName: "Typed signature name",
    drawHere: "Draw signature here",
    clearDrawing: "Clear drawing",
    downloadedSvg: "Download drawn SVG",
    svgStorageKey: "SVG storage key",
    imageStorageKey: "Image storage key",
    uploadedPreview: "Uploaded image preview",
    assetKeyHelp:
      "Your backend validates drawn/uploaded signatures with a storage key. Paste the key returned by your asset upload layer.",
    submit: "Submit e-signature workflow",
    submitting: "Processing...",
    signInTitle: "Sign in required",
    signInDescription: "E-signature uses your authenticated analyzer quota and audit context.",
    signIn: "Sign in",
    loading: "Checking account...",
    resultTitle: "Envelope result",
    envelopeId: "Envelope ID",
    status: "Status",
    downloadSigned: "Download signed PDF",
    downloadCertificate: "Download certificate",
    openPreview: "Open preview",
    noFile: "Choose a PDF file.",
    invalidFile: "Only PDF files are supported.",
    tooLarge: "The PDF must be 50 MB or smaller.",
    badEmail: "Enter a valid email address.",
    badName: "Name is required.",
    badRecipient: "Recipient details are incomplete.",
    badRectangle: "Rectangle values must be normalized and stay inside the page.",
    badSignature: "Complete the selected signature source.",
    failed: "Could not process the e-signature request.",
  },
  fr: {
    back: "Retour",
    badge: "ReDOCX Sign",
    title: "Signer des PDF avec signature typée, dessinée ou téléversée",
    description:
      "Créez un flux de signature personnelle ou envoyez un PDF à signer selon le contrat backend.",
    uploadTitle: "PDF source",
    uploadHelp: "Autorisé : .pdf uniquement. Maximum 50 Mo.",
    chooseFile: "Choisir un PDF",
    workflow: "Flux",
    selfSign: "Signer maintenant",
    sendSingle: "Envoyer à un destinataire",
    sendMultiple: "Envoyer à plusieurs destinataires",
    selfSignThenSend: "Signer puis envoyer",
    routingMode: "Mode d’acheminement",
    sequential: "Séquentiel",
    parallel: "Parallèle",
    signerDetails: "Propriétaire / signataire",
    signerName: "Nom du signataire",
    signerEmail: "Email du signataire",
    recipients: "Destinataires",
    recipientName: "Nom du destinataire",
    recipientEmail: "Email du destinataire",
    signingOrder: "Ordre",
    addRecipient: "Ajouter un destinataire",
    fields: "Champs",
    addField: "Ajouter un champ",
    fieldType: "Type de champ",
    assignedTo: "Assigné à",
    pageNumber: "Page",
    rectangle: "Rectangle normalisé",
    x: "X",
    y: "Y",
    width: "Largeur",
    height: "Hauteur",
    emailSubject: "Objet de l’email",
    emailMessage: "Message email",
    expiresInDays: "Expire dans jours",
    sendEmails: "Envoyer les invitations",
    signatureSource: "Source de signature",
    typed: "Typée",
    drawn: "Dessinée",
    uploaded: "Image téléversée",
    typedName: "Nom de signature typée",
    drawHere: "Dessinez ici",
    clearDrawing: "Effacer",
    downloadedSvg: "Télécharger le SVG",
    svgStorageKey: "Clé de stockage SVG",
    imageStorageKey: "Clé de stockage image",
    uploadedPreview: "Aperçu de l’image",
    assetKeyHelp:
      "Le backend valide les signatures dessinées/téléversées avec une clé de stockage. Collez la clé retournée par votre couche d’upload d’assets.",
    submit: "Soumettre le flux de signature",
    submitting: "Traitement...",
    signInTitle: "Connexion requise",
    signInDescription: "La signature utilise votre quota authentifié et le contexte d’audit.",
    signIn: "Se connecter",
    loading: "Vérification du compte...",
    resultTitle: "Résultat de l’enveloppe",
    envelopeId: "ID enveloppe",
    status: "Statut",
    downloadSigned: "Télécharger le PDF signé",
    downloadCertificate: "Télécharger le certificat",
    openPreview: "Ouvrir l’aperçu",
    noFile: "Choisissez un PDF.",
    invalidFile: "Seuls les PDF sont pris en charge.",
    tooLarge: "Le PDF doit faire 50 Mo ou moins.",
    badEmail: "Saisissez un email valide.",
    badName: "Le nom est requis.",
    badRecipient: "Les informations du destinataire sont incomplètes.",
    badRectangle: "Le rectangle doit être normalisé et rester dans la page.",
    badSignature: "Complétez la source de signature choisie.",
    failed: "Impossible de traiter la demande de signature.",
  },
};

export const splitPdfPageTranslations = {
  en: { back: "Back", badge: "PDF tools", title: "Split PDF files", description: "Split a PDF by every page, selected pages, or page ranges. Signature appearances already present in extracted pages are preserved as PDF content.", uploadTitle: "Source PDF", uploadHelp: "Allowed: .pdf only. Maximum 50 MB.", chooseFile: "Choose PDF", mode: "Split mode", everyPage: "Every page", selectedPages: "Selected pages", pageRanges: "Page ranges", selectedPagesInput: "Selected pages, e.g. 1,3,5", pageRangesInput: "Page ranges, e.g. 1-3,5-7", outputBasename: "Output basename", split: "Split PDF", splitting: "Splitting...", resultTitle: "Split result ready", downloadArchive: "Download ZIP archive", downloadFile: "Download file", signInTitle: "Sign in required", signInDescription: "PDF tools use your authenticated analyzer quota.", signIn: "Sign in", loading: "Checking account...", noFile: "Choose a PDF file.", invalidFile: "Only PDF files are supported.", tooLarge: "The PDF must be 50 MB or smaller.", badSelected: "Selected pages are required for this mode.", badRanges: "Page ranges are required for this mode.", failed: "Could not split PDF." },
  fr: { back: "Retour", badge: "Outils PDF", title: "Diviser des PDF", description: "Divisez un PDF par page, pages choisies ou plages. Les signatures déjà présentes sont préservées comme contenu PDF.", uploadTitle: "PDF source", uploadHelp: "Autorisé : .pdf uniquement. Maximum 50 Mo.", chooseFile: "Choisir un PDF", mode: "Mode de division", everyPage: "Chaque page", selectedPages: "Pages sélectionnées", pageRanges: "Plages", selectedPagesInput: "Pages, ex. 1,3,5", pageRangesInput: "Plages, ex. 1-3,5-7", outputBasename: "Nom de base", split: "Diviser", splitting: "Division...", resultTitle: "Résultat prêt", downloadArchive: "Télécharger le ZIP", downloadFile: "Télécharger", signInTitle: "Connexion requise", signInDescription: "Les outils PDF utilisent votre quota authentifié.", signIn: "Se connecter", loading: "Vérification du compte...", noFile: "Choisissez un PDF.", invalidFile: "Seuls les PDF sont pris en charge.", tooLarge: "Le PDF doit faire 50 Mo ou moins.", badSelected: "Les pages sélectionnées sont requises.", badRanges: "Les plages sont requises.", failed: "Impossible de diviser le PDF." },
};


export const generateQuestionsPageTranslations = {
  en: {
    badge: "Generate questions",
    title: "Generate study questions, then answers when you choose",
    description:
      "Upload a PDF or Word document, or paste inline text. ReDOCX generates numbered questions from your content first, then asks whether to generate matching answers.",
    fileMode: "Upload file",
    textMode: "Inline text",
    uploadTitle: "Upload content for question generation",
    allowedFileInputs:
      "Allowed: .pdf and .docx. Rejected automatically: .png, .jpg, .jpeg, and unsupported formats.",
    outputExtensionWillBe: "Output extension will be",
    pasteTextLabel: "Paste text to generate questions from",
    pasteTextPlaceholder: "Paste or type the source text here...",
    inlineTextTreatedAs:
      "Inline text is treated as .txt, so generated questions and answers can be shown inline.",
    unsupportedFileType:
      "Unsupported file type: {ext}. Only .pdf and .docx uploads are allowed. PNG, JPG, JPEG and other image formats are rejected.",
    fileTooLarge: "File is too large. Maximum allowed size is {maxSize} MB.",
    sourceRequired: "Please provide a supported document or inline text.",
    questionsFailed: "Something went wrong while generating questions.",
    answersFailed: "Something went wrong while generating answers.",
    generatingQuestions: "Generating questions...",
    generatingAnswers: "Generating answers...",
    generateQuestions: "Generate questions",
    generateAnswers: "Generate answers",
    skipAnswers: "No, keep questions only",
    resetFlow: "Start over",
    formatPolicy: "Format policy",
    policySubtitle: "Two-step generation with strict source grounding",
    allowedUploadsLabel: "Allowed uploads:",
    inlineInputLabel: "Inline input:",
    rejectedAutomaticallyLabel: "Rejected automatically:",
    outputRuleLabel: "Output rule:",
    inlineInputValue: "treated as .txt",
    rejectedAutomaticallyValue: ".png, .jpg, .jpeg, and unsupported file types",
    outputRuleValue:
      "questions are generated first; answers are generated only after user confirmation",
    questionsOutputTitle: "Generated questions",
    answersOutputTitle: "Generated answers",
    previewEmpty:
      "Your generated questions will appear here. After that, you can decide whether ReDOCX should generate answers.",
    answersPreviewEmpty:
      "Answers will appear here only if you choose to generate them.",
    outputExtensionLabel: "Output extension:",
    inputFile: "Input file",
    inputText: "Inline text",
    detectedSource: "Source",
    questionCount: "Question count",
    answerCount: "Answer count",
    downloadQuestionsFile: "Download generated questions",
    downloadAnswersFile: "Download generated answers",
    answerPromptTitle: "Do you want me to generate answers to these questions?",
    answerPromptDescription:
      "Answer generation uses the original source content and the exact numbered questions just generated.",
    declinedTitle: "Questions kept without answers",
    declinedDescription:
      "No answer-generation request was sent. You can still generate answers from these questions while this page remains open.",
    cannotGenerateAnswersTitle: "Question text is unavailable",
    cannotGenerateAnswersDescription:
      "The questions file was generated, but the frontend did not receive the numbered question text. Apply the included backend route patch so uploaded PDF/DOCX flows return a generated_questions_text side channel.",
    backendPatchNotice:
      "For uploaded PDF/DOCX sources, this UI expects the patched backend route to return generated_questions_text alongside the downloadable questions file.",
    badQuestionList:
      "The generated questions were not returned as a sequential numbered list. Please regenerate questions before generating answers.",
    missingDownloadUrl:
      "Processing finished, but the backend did not return a download URL.",
    fileAccepted: "File accepted",
    wordsLimit: "Maximum source length: 1,000 extracted words.",
    yes: "Yes, generate answers",
    no: "No",
  },
  fr: {
    badge: "Générer des questions",
    title: "Générez des questions, puis des réponses si vous le choisissez",
    description:
      "Téléversez un PDF ou un document Word, ou collez du texte inline. ReDOCX génère d’abord des questions numérotées à partir de votre contenu, puis demande si vous voulez générer les réponses correspondantes.",
    fileMode: "Téléverser un fichier",
    textMode: "Texte inline",
    uploadTitle: "Téléverser le contenu pour générer des questions",
    allowedFileInputs:
      "Autorisés : .pdf et .docx. Rejetés automatiquement : .png, .jpg, .jpeg et les formats non pris en charge.",
    outputExtensionWillBe: "L’extension de sortie sera",
    pasteTextLabel: "Coller le texte source",
    pasteTextPlaceholder: "Collez ou saisissez le texte source ici...",
    inlineTextTreatedAs:
      "Le texte inline est traité comme .txt, donc les questions et réponses peuvent être affichées inline.",
    unsupportedFileType:
      "Type de fichier non pris en charge : {ext}. Seuls les fichiers .pdf et .docx sont autorisés. PNG, JPG, JPEG et les autres formats image sont rejetés.",
    fileTooLarge: "Le fichier est trop volumineux. La taille maximale autorisée est de {maxSize} Mo.",
    sourceRequired: "Veuillez fournir un document pris en charge ou du texte inline.",
    questionsFailed: "Une erreur s’est produite lors de la génération des questions.",
    answersFailed: "Une erreur s’est produite lors de la génération des réponses.",
    generatingQuestions: "Génération des questions...",
    generatingAnswers: "Génération des réponses...",
    generateQuestions: "Générer les questions",
    generateAnswers: "Générer les réponses",
    skipAnswers: "Non, garder seulement les questions",
    resetFlow: "Recommencer",
    formatPolicy: "Règles de format",
    policySubtitle: "Génération en deux étapes strictement fondée sur la source",
    allowedUploadsLabel: "Téléversements autorisés :",
    inlineInputLabel: "Entrée inline :",
    rejectedAutomaticallyLabel: "Rejetés automatiquement :",
    outputRuleLabel: "Règle de sortie :",
    inlineInputValue: "traité comme .txt",
    rejectedAutomaticallyValue: ".png, .jpg, .jpeg et formats non pris en charge",
    outputRuleValue:
      "les questions sont générées d’abord ; les réponses ne sont générées qu’après confirmation de l’utilisateur",
    questionsOutputTitle: "Questions générées",
    answersOutputTitle: "Réponses générées",
    previewEmpty:
      "Vos questions générées apparaîtront ici. Ensuite, vous pourrez décider si ReDOCX doit générer les réponses.",
    answersPreviewEmpty:
      "Les réponses apparaîtront ici uniquement si vous choisissez de les générer.",
    outputExtensionLabel: "Extension de sortie :",
    inputFile: "Fichier d’entrée",
    inputText: "Texte inline",
    detectedSource: "Source",
    questionCount: "Nombre de questions",
    answerCount: "Nombre de réponses",
    downloadQuestionsFile: "Télécharger les questions générées",
    downloadAnswersFile: "Télécharger les réponses générées",
    answerPromptTitle: "Voulez-vous que je génère les réponses à ces questions ?",
    answerPromptDescription:
      "La génération des réponses utilise le contenu source original et les questions numérotées exactes qui viennent d’être générées.",
    declinedTitle: "Questions conservées sans réponses",
    declinedDescription:
      "Aucune requête de génération de réponses n’a été envoyée. Vous pouvez encore générer les réponses à partir de ces questions tant que cette page reste ouverte.",
    cannotGenerateAnswersTitle: "Le texte des questions est indisponible",
    cannotGenerateAnswersDescription:
      "Le fichier de questions a été généré, mais le frontend n’a pas reçu le texte numéroté. Appliquez le correctif backend inclus pour que les flux PDF/DOCX retournent generated_questions_text.",
    backendPatchNotice:
      "Pour les sources PDF/DOCX téléversées, cette interface attend que la route backend corrigée retourne generated_questions_text avec le fichier de questions téléchargeable.",
    badQuestionList:
      "Les questions générées ne sont pas revenues sous forme de liste numérotée séquentielle. Veuillez régénérer les questions avant de générer les réponses.",
    missingDownloadUrl:
      "Le traitement est terminé, mais le backend n’a pas renvoyé d’URL de téléchargement.",
    fileAccepted: "Fichier accepté",
    wordsLimit: "Longueur maximale de la source : 1 000 mots extraits.",
    yes: "Oui, générer les réponses",
    no: "Non",
  },
};


export const billingPageTranslations = {
  en: {
    back: "Back",
    badge: "Billing & Upgrade",
    title: "Choose the right ReDOCX plan",
    description:
      "Review your current plan and see the plans available from your account. Upgrade buttons are shown only for valid upgrade paths from your current entitlement.",
    yourPlan: "Your plan",
    currentPlan: "Current plan",
    upgrade: "Upgrade",
    creatingUpgrade: "Preparing upgrade...",
    loading: "Loading billing options...",
    loadFailed: "Could not load billing options.",
    upgradeFailed: "Could not start the upgrade.",
    checkoutNotConfigured:
      "This upgrade is allowed, but checkout is not configured yet.",
    signInTitle: "Sign in required",
    signInDescription:
      "Billing and upgrade options are available only for authenticated users.",
    signIn: "Sign in",
  },
  fr: {
    back: "Retour",
    badge: "Facturation & mise à niveau",
    title: "Choisissez le bon forfait ReDOCX",
    description:
      "Consultez votre forfait actuel et les forfaits disponibles pour votre compte. Les boutons de mise à niveau ne s’affichent que pour les parcours autorisés depuis votre forfait actuel.",
    yourPlan: "Votre forfait",
    currentPlan: "Forfait actuel",
    upgrade: "Mettre à niveau",
    creatingUpgrade: "Préparation...",
    loading: "Chargement des options de facturation...",
    loadFailed: "Impossible de charger les options de facturation.",
    upgradeFailed: "Impossible de démarrer la mise à niveau.",
    checkoutNotConfigured:
      "Cette mise à niveau est autorisée, mais le paiement n’est pas encore configuré.",
    signInTitle: "Connexion requise",
    signInDescription:
      "Les options de facturation et de mise à niveau sont réservées aux utilisateurs authentifiés.",
    signIn: "Se connecter",
  },
};
