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
        "If you are on a paid plan, ReDOCX will first check whether you must leave a team, transfer ownership, or wait until the subscription period ends before final deletion.",
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
    teamSettings: "Team settings",
    help: {
      label: "Help",
      privacyPolicy: "Privacy Policy",
      termsOfUse: "Terms of Use",
    },
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
    upgradeToUse: "Upgrade to use",
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
        key: "sensitiveDataProtection",
        name: "Sensitive Data Protection",
        route: "/sensitive-data-protection",
        description:
          "Protect sensitive information with dedicated redaction and data masking workflows.",
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
        key: "vault",
        name: "Vault",
        route: "/vault",
        description:
          "Store private files and notes in encrypted, owner-scoped storage.",
      },
      {
        key: "pdfTools",
        name: "PDF Tools",
        route: "/pdf-tools",
        requiresAuth: false,
        description:
          "Combine, compress, edit, split, and lock PDFs. Guests receive one PDF Tools request before sign-in.",
      },
      {
        key: "textToSpeech",
        name: "Text to Speech",
        route: "/text-to-speech",
        description:
          "Turn PDF, Word, TXT, or typed text into downloadable speech audio.",
      },
      {
        key: "voiceAgent",
        name: "Voice Agent",
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
      error:
        "Impossible de lancer le changement de mot de passe. Veuillez réessayer.",
    },
    deleteAccount: {
      label: "Supprimer mon compte",
      title: "Supprimer votre compte ?",
      description:
        "Si vous avez un forfait payant, ReDOCX vérifiera d’abord si vous devez quitter une équipe, transférer la propriété ou attendre la fin de la période d’abonnement avant la suppression définitive.",
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
    teamSettings: "Paramètres de l’équipe",
    help: {
      label: "Aide",
      privacyPolicy: "Politique de confidentialité",
      termsOfUse: "Conditions d’utilisation",
    },
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
    upgradeToUse: "Passez à une offre supérieure pour utiliser",
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
        key: "sensitiveDataProtection",
        name: "Protection des données sensibles",
        route: "/sensitive-data-protection",
        description:
          "Protégez les informations sensibles grâce à des workflows dédiés de caviardage et de masquage des données.",
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
        key: "vault",
        name: "Coffre-fort",
        route: "/vault",
        description:
          "Stockez des fichiers et notes privés dans un espace chiffré associé à votre compte.",
      },
      {
        key: "pdfTools",
        name: "Outils PDF",
        route: "/pdf-tools",
        requiresAuth: false,
        description:
          "Combinez, compressez, modifiez, divisez et verrouillez des PDF. Les visiteurs disposent d’une requête Outils PDF avant connexion.",
      },
      {
        key: "textToSpeech",
        name: "Synthèse vocale",
        route: "/text-to-speech",
        description:
          "Transformez des PDF, documents Word, fichiers TXT ou du texte saisi en audio téléchargeable.",
      },
      {
        key: "voiceAgent",
        name: "Agent vocal",
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
export const sensitiveDataProtectionPageTranslations = {
  en: {
    back: "Back",
    badge: "Privacy & protection",
    title: "Sensitive Data Protection",
    description:
      "Protect sensitive information in documents with focused redaction and data masking workflows.",
    loading: "Checking account...",
    signInTitle: "Sign in required",
    signInDescription:
      "Sensitive data protection tools use your authenticated ReDOCX quota.",
    signIn: "Sign in",
    actions: [
      {
        key: "redact",
        name: "Redaction",
        route: "/sensitive-data-protection/redact",
        description:
          "Redact sensitive data and information while preserving the document’s original layout and structure.",
      },
      {
        key: "mask",
        name: "Data Masking",
        route: "/sensitive-data-protection/data-mask",
        description:
          "Mask sensitive data and information while keeping the document readable and usable.",
      },
    ],
  },
  fr: {
    back: "Retour",
    badge: "Confidentialité & protection",
    title: "Protection des données sensibles",
    description:
      "Protégez les informations sensibles de vos documents avec des workflows ciblés de caviardage et de masquage des données.",
    loading: "Vérification du compte...",
    signInTitle: "Connexion requise",
    signInDescription:
      "Les outils de protection des données sensibles utilisent votre quota ReDOCX authentifié.",
    signIn: "Se connecter",
    actions: [
      {
        key: "redact",
        name: "Caviardage",
        route: "/sensitive-data-protection/redact",
        description:
          "Caviardez les données et informations sensibles tout en préservant la mise en page et la structure d’origine du document.",
      },
      {
        key: "mask",
        name: "Masquage des données",
        route: "/sensitive-data-protection/data-mask",
        description:
          "Masquez les données et informations sensibles tout en conservant un document lisible et exploitable.",
      },
    ],
  },
};

export const convertPageTranslations = {
  en: {
    badge: "Convert documents, files and images",
    title: "Convert files across several formats",
    description: "Upload PDF, Word, Excel, PowerPoint, HTML, JPG, JPEG, or PNG",
    uploadTitle: "Upload file or document",
    conversionOutput: "Conversion result",
    previewText: "Download appears here after file conversion",

    unsupportedFileType:
      "Unsupported file type: {ext}. Allowed: .pdf, .docx, .xlsx, .pptx, .html, .htm, .jpg, .jpeg, and .png",
    fileTooLarge: "File is too large, maximum allowed size is {maxSize} MB",
    chooseFileToConvert: "Please choose a file to convert",
    invalidConversion: "This conversion combination is not allowed",
    conversionPotentialIssue: "Something went wrong while converting the file",
    missingDownloadUrl:
      "Conversion finished, but the backend did not return a download URL",

    conversionCompleted: "Conversion completed",
    inputFile: "Input file(s)",
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
    excelWorkbook: "Excel workbook",
    htmlDocument: "HTML document",
    powerPointPresentation: "PowerPoint presentation",
    unknownFile: "Unknown file",
  },
  fr: {
    badge: "Convertir des documents, fichiers et images",
    title: "Convertir des fichiers dans plusieurs formats",
    description:
      "Téléversez un PDF, un document Word, Excel, PowerPoint, HTML, JPG, JPEG ou PNG",
    uploadTitle: "Téléverser un fichier ou un document",
    conversionOutput: "Résultat de la conversion",
    previewText:
      "Le téléchargement apparaîtra ici après la conversion du fichier",

    unsupportedFileType:
      "Type de fichier non pris en charge: {ext}. Formats autorisés : .pdf, .docx, .xlsx, .pptx, .html, .htm, .jpg, .jpeg et .png",
    fileTooLarge:
      "Le fichier est trop volumineux, la taille maximale autorisée est de {maxSize} Mo",
    chooseFileToConvert: "Veuillez choisir un fichier à convertir",
    invalidConversion: "Cette combinaison de conversion n’est pas autorisée",
    conversionPotentialIssue:
      "Une erreur s’est produite lors de la conversion du fichier",
    missingDownloadUrl:
      "La conversion est terminée, mais le backend n’a pas renvoyé d’URL de téléchargement",

    conversionCompleted: "Conversion terminée",
    inputFile: "Fichier(s) d’entrée",
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
    excelWorkbook: "Classeur Excel",
    htmlDocument: "Document HTML",
    powerPointPresentation: "Présentation PowerPoint",
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
    explanationPotentialIssue:
      "Something went wrong while generating the explanation.",

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
    explanationPotentialIssue:
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
    summaryPotentialIssue: "Something went wrong while generating the summary.",

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
    summaryPotentialIssue:
      "Une erreur s’est produite lors de la génération du résumé.",

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
      "Select a supported target language",
    targetLanguageHelp:
      "Choose from the languages ReDOCX explicitly supports. Each option uses a canonical language tag and a translation quality-review pass.",

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
    translationPotentialIssue:
      "Something went wrong while generating the translation.",

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

    languageOptions: [
      { code: "en", label: "English" },
      { code: "fr", label: "French" },
      { code: "es", label: "Spanish" },
      { code: "de", label: "German" },
      { code: "pt-PT", label: "European Portuguese" },
      { code: "pt-BR", label: "Brazilian Portuguese" },
      { code: "ar", label: "Arabic" },
      { code: "zh-Hans", label: "Simplified Chinese" },
      { code: "zh-Hant", label: "Traditional Chinese" },
      { code: "ja", label: "Japanese" },
      { code: "ko", label: "Korean" },
      { code: "hi", label: "Hindi" },
      { code: "yo", label: "Yoruba" },
      { code: "ha", label: "Hausa" },
      { code: "ig", label: "Igbo" },
      { code: "sw", label: "Swahili" },
      { code: "tr", label: "Turkish" },
      { code: "ru", label: "Russian" },
      { code: "it", label: "Italian" },
      { code: "nl", label: "Dutch" },
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
      "Sélectionnez une langue cible prise en charge",
    targetLanguageHelp:
      "Choisissez parmi les langues explicitement prises en charge par ReDOCX. Chaque option utilise une balise de langue canonique et une passe de contrôle qualité.",

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
    translationPotentialIssue:
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

    languageOptions: [
      { code: "en", label: "Anglais" },
      { code: "fr", label: "Français" },
      { code: "es", label: "Espagnol" },
      { code: "de", label: "Allemand" },
      { code: "pt-PT", label: "Portugais européen" },
      { code: "pt-BR", label: "Portugais brésilien" },
      { code: "ar", label: "Arabe" },
      { code: "zh-Hans", label: "Chinois simplifié" },
      { code: "zh-Hant", label: "Chinois traditionnel" },
      { code: "ja", label: "Japonais" },
      { code: "ko", label: "Coréen" },
      { code: "hi", label: "Hindi" },
      { code: "yo", label: "Yoruba" },
      { code: "ha", label: "Haoussa" },
      { code: "ig", label: "Igbo" },
      { code: "sw", label: "Swahili" },
      { code: "tr", label: "Turc" },
      { code: "ru", label: "Russe" },
      { code: "it", label: "Italien" },
      { code: "nl", label: "Néerlandais" },
    ],
  },
};
export const transcribePageTranslations = {
  en: {
    badge: "Transcription",
    title: "Transcribe audio, video, or live speech",
    description:
      "Upload audio/video or record directly from your device microphone, then transcribe through the same secure processing pipeline.",
    uploadTitle: "Upload audio or video",
    allowedFileInputs:
      "Allowed inputs: .mp3, .wav, .aac, .flac, .m4a, .ogg, .mp4, .mov, .avi, .mkv, .wmv, .webm",
    microphoneTitle: "Speak directly",
    microphoneHelp:
      "Use your device microphone to record speech, then transcribe the recording.",
    startRecording: "Start recording",
    stopRecording: "Stop recording",
    recordingNow: "Recording",
    preparingMicrophone: "Preparing recording",
    microphoneIdle: "Microphone access starts only when you press Start recording.",
    microphoneRecordingReady: "Recording captured and ready to transcribe.",
    microphoneLimit: "Maximum recording: {maxDuration} and {maxSize} MB",
    microphonePermissionDenied:
      "Microphone access was denied. Allow microphone permission for this site and try again.",
    microphoneNotFound: "No microphone was detected on this device.",
    microphoneUnavailable:
      "The microphone is currently unavailable or is being used by another application.",
    microphoneUnsupported:
      "This browser does not support secure microphone recording for transcription.",
    microphoneSecureContextRequired:
      "Microphone recording requires a secure HTTPS connection (or localhost during development).",
    microphoneFormatUnsupported:
      "This browser cannot record in a media format supported by the Transcribe service.",
    microphoneRecordingFailed:
      "The microphone recording could not be completed. Please try again.",
    stopRecordingBeforeUpload:
      "Stop the current microphone recording before selecting upload files.",
    sourceLabel: "Source:",
    microphoneSourceLabel: "Microphone recording",
    unsupportedFileType: "Unsupported file type: {ext}",
    fileTooLarge:
      "File is too large, maximum size for this media type is {maxSize} MB",
    mediaTooLong:
      "Media is too long, maximum duration for this media type is {maxDuration}",
    couldNotReadDuration:
      "Could not read media duration, Please try another file",
    chooseFileToTranscribe: "Please choose an audio/video file or record speech with your microphone",
    transcriptionPotentialIssue: "Transcription request failed",
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
    transcriptMetaValue: "Inline text, synchronized subtitles, and .pdf download",
    synchronizedPlaybackTitle: "Synchronized media playback",
    subtitlesLabel: "Timed subtitles",
    downloadPdfTranscript: "Download PDF transcript",
    detectedTypeLabel: "Detected media type",
    durationLabel: "Duration",
    audioType: "Audio",
    videoType: "Video",
    unknownType: "Unknown",
  },
  fr: {
    badge: "Transcription",
    title: "Transcrire l’audio, la vidéo ou la parole en direct",
    description:
      "Téléversez un fichier audio/vidéo ou enregistrez directement depuis le microphone de votre appareil, puis utilisez le même pipeline sécurisé de transcription.",
    uploadTitle: "Téléverser un fichier audio ou vidéo",
    allowedFileInputs:
      "Entrées autorisées : .mp3, .wav, .aac, .flac, .m4a, .ogg, .mp4, .mov, .avi, .mkv, .wmv, .webm",
    microphoneTitle: "Parler directement",
    microphoneHelp:
      "Utilisez le microphone de votre appareil pour enregistrer votre voix, puis transcrivez l’enregistrement.",
    startRecording: "Démarrer l’enregistrement",
    stopRecording: "Arrêter l’enregistrement",
    recordingNow: "Enregistrement",
    preparingMicrophone: "Préparation de l’enregistrement",
    microphoneIdle:
      "L’accès au microphone ne commence que lorsque vous appuyez sur Démarrer l’enregistrement.",
    microphoneRecordingReady: "Enregistrement capturé et prêt à transcrire.",
    microphoneLimit: "Enregistrement maximal : {maxDuration} et {maxSize} Mo",
    microphonePermissionDenied:
      "L’accès au microphone a été refusé. Autorisez le microphone pour ce site puis réessayez.",
    microphoneNotFound: "Aucun microphone n’a été détecté sur cet appareil.",
    microphoneUnavailable:
      "Le microphone est actuellement indisponible ou utilisé par une autre application.",
    microphoneUnsupported:
      "Ce navigateur ne prend pas en charge l’enregistrement sécurisé du microphone pour la transcription.",
    microphoneSecureContextRequired:
      "L’enregistrement du microphone nécessite une connexion HTTPS sécurisée (ou localhost en développement).",
    microphoneFormatUnsupported:
      "Ce navigateur ne peut pas enregistrer dans un format multimédia pris en charge par le service de transcription.",
    microphoneRecordingFailed:
      "L’enregistrement du microphone n’a pas pu être terminé. Veuillez réessayer.",
    stopRecordingBeforeUpload:
      "Arrêtez l’enregistrement microphone en cours avant de sélectionner des fichiers à téléverser.",
    sourceLabel: "Source :",
    microphoneSourceLabel: "Enregistrement microphone",
    unsupportedFileType: "Type de fichier non pris en charge: {ext}",
    fileTooLarge:
      "Le fichier est trop volumineux, la taille maximale pour ce type de média est de {maxSize} Mo",
    mediaTooLong:
      "Le média est trop long, la durée maximale pour ce type de média est de {maxDuration}",
    couldNotReadDuration:
      "Impossible de lire la durée du média, veuillez essayer un autre fichier",
    chooseFileToTranscribe: "Veuillez choisir un fichier audio/vidéo ou enregistrer votre voix avec le microphone",
    transcriptionPotentialIssue: "La requête de transcription a échoué",
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
    transcriptMetaValue: "Texte inline, sous-titres synchronisés et téléchargement PDF",
    synchronizedPlaybackTitle: "Lecture multimédia synchronisée",
    subtitlesLabel: "Sous-titres minutés",
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
    redactionPotentialIssue: "Something went wrong while processing redaction.",
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
    detectingDocumentType: "Detecting document type...",
    documentTypeAutoDetected: "Auto-detected ({confidence}% confidence). You can override it if needed.",
    documentTypeDetectionFallback: "A reliable type could not be determined. General document was selected; you can override it.",
    sensitiveTargetsLabel: "Sensitive data to redact",
    coverageNote:
      "National / government ID includes country-specific identifiers such as US SSNs, Canadian SINs, UK NINs, and equivalent supported IDs. Signature includes typed and visual signatures.",
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
    inputFile: "Input file(s)",
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
    redactionPotentialIssue:
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
    detectingDocumentType: "Détection automatique du type de document...",
    documentTypeAutoDetected: "Détecté automatiquement ({confidence}% de confiance). Vous pouvez le modifier si nécessaire.",
    documentTypeDetectionFallback: "Aucun type suffisamment fiable n’a pu être déterminé. Document général a été sélectionné ; vous pouvez le modifier.",
    sensitiveTargetsLabel: "Données sensibles à caviarder",
    coverageNote:
      "L’identifiant national / officiel comprend les identifiants propres à chaque pays, tels que le SSN américain, le NAS canadien, le NIN britannique et leurs équivalents pris en charge. La signature comprend les signatures saisies et visuelles.",
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
    inputFile: "Fichier(s) d’entrée",
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
    maskingPotentialIssue:
      "Something went wrong while processing data masking.",
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
    detectingDocumentType: "Detecting document type...",
    documentTypeAutoDetected: "Auto-detected ({confidence}% confidence). You can override it if needed.",
    documentTypeDetectionFallback: "A reliable type could not be determined. General document was selected; you can override it.",
    sensitiveTargetsLabel: "Sensitive data to mask",
    coverageNote:
      "National / government ID includes country-specific identifiers such as US SSNs, Canadian SINs, UK NINs, and equivalent supported IDs. Signature includes typed and visual signatures.",
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
    inputFile: "Input file(s)",
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
    maskingPotentialIssue:
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
    detectingDocumentType: "Détection automatique du type de document...",
    documentTypeAutoDetected: "Détecté automatiquement ({confidence}% de confiance). Vous pouvez le modifier si nécessaire.",
    documentTypeDetectionFallback: "Aucun type suffisamment fiable n’a pu être déterminé. Document général a été sélectionné ; vous pouvez le modifier.",
    sensitiveTargetsLabel: "Données sensibles à masquer",
    coverageNote:
      "L’identifiant national / officiel comprend les identifiants propres à chaque pays, tels que le SSN américain, le NAS canadien, le NIN britannique et leurs équivalents pris en charge. La signature comprend les signatures saisies et visuelles.",
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
    inputFile: "Fichier(s) d’entrée",
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
    allowedFileInputs:
      "Allowed inputs: .pdf, .docx, .jpg, .jpeg, .png. Upload 1 to {maxFiles} documents.",
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
    documentClassRequired:
      "Choose Auto-detect or select at least one document type",
    extractionPotentialIssue:
      "Something went wrong while extracting structured data",
    missingDownloadUrl:
      "Extraction finished, but the backend did not return a download URL",

    detectedType: "Detected type:",
    outputFormatLabel: "Download format",
    outputFormatHelp: "Choose the file format you want to download",
    outputFormatExamples:
      "Examples: JSON for apps, CSV for spreadsheets, Excel for review workbooks",
    resultShapeLabel: "Output layout",
    resultShapeHelp: "Choose how the extracted data should be organized",
    resultShapeExamples:
      "Not sure? Keep Advanced options closed and use the Excel-ready default.",
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

    inputFile: "Input file(s)",
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

    structuredExtractionUx: {
      documentTypeLabel: "Document type",
      autoDetectDocumentType: "Auto-detect document type",
      autoDetectDocumentTypeHelp:
        "Recommended. ReDOCX will inspect the file and use the best matching extraction strategy.",
      advancedOptions: "Advanced options",
      advancedOptionsHelp:
        "Use these only when you need a specific output format, result shape, document class, or exact fields.",
      simpleFlowHelp:
        "Upload a document, let ReDOCX detect the type, then download an Excel-ready extraction.",
      outputFormatLabels: {
        json: "Developer JSON",
        csv: "CSV spreadsheet",
        xlsx: "Excel workbook",
      },
      resultShapeLabels: {
        machine_readable: "Full technical JSON",
        key_value_fields: "Simple fields",
        tables: "Tables only",
        row_based_records: "Spreadsheet rows",
      },
      previewGeneratedTitle: "Generated preview",
      previewGeneratedBody:
        "Review the extracted data before downloading the file.",
      previewCoverage:
        "Showing {rowCount} extracted row(s) across {columnCount} column(s).",
      viewStructuredJson: "View structured JSON",
      previewShortened:
        "Preview shortened. Download the full file to see all rows.",
      selectedFieldStatusTitle: "Selected field status",
      selectedFieldStatusHelp:
        "Requested fields are marked as found, not found, or low confidence with evidence when available.",
      fieldStatusFound: "Found",
      fieldStatusNotFound: "Not found",
      fieldStatusLowConfidence: "Low confidence",
      fieldStatusEvidence: "Evidence",
      fieldStatusNoEvidence: "No evidence excerpt available",
      fieldStatusValue: "Value",
      extractionQuality: "Extraction coverage",
      reviewNotes: "Review notes",
      qualityHelp:
        "Coverage shows how many requested fields were found. Always compare important values with the source.",
      notApplicable: "Not applicable",
    },

    outputFormatLabels: {
      json: ".json",
      csv: ".csv",
      xlsx: ".xlsx",
    },

    resultShapeLabels: {
      machine_readable: "Full technical JSON",
      key_value_fields: "Simple fields",
      tables: "Tables only",
      row_based_records: "Spreadsheet rows",
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
    allowedFileInputs:
      "Entrées autorisées : .pdf, .docx, .jpg, .jpeg, .png. Téléversez 1 à {maxFiles} documents.",
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
    documentClassRequired:
      "Choisissez la détection automatique ou sélectionnez au moins un type de document",
    extractionPotentialIssue:
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
      "Vous hésitez ? Gardez les options avancées fermées et utilisez le réglage prêt pour Excel.",
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

    inputFile: "Fichier(s) d’entrée",
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

    structuredExtractionUx: {
      documentTypeLabel: "Type de document",
      autoDetectDocumentType: "Détecter automatiquement le type de document",
      autoDetectDocumentTypeHelp:
        "Recommandé. ReDOCX inspecte le fichier et applique la meilleure stratégie d’extraction.",
      advancedOptions: "Options avancées",
      advancedOptionsHelp:
        "Utilisez ces options uniquement si vous avez besoin d’un format de sortie, d’une structure, d’une classe de document ou de champs précis.",
      simpleFlowHelp:
        "Importez un document, laissez ReDOCX détecter le type, puis téléchargez une extraction prête pour Excel.",
      outputFormatLabels: {
        json: "JSON développeur",
        csv: "Tableur CSV",
        xlsx: "Classeur Excel",
      },
      resultShapeLabels: {
        machine_readable: "JSON technique complet",
        key_value_fields: "Champs simples",
        tables: "Tableaux uniquement",
        row_based_records: "Lignes de tableur",
      },
      previewGeneratedTitle: "Aperçu généré",
      previewGeneratedBody:
        "Vérifiez les données extraites avant de télécharger le fichier.",
      previewCoverage:
        "Affichage de {rowCount} ligne(s) extraite(s) sur {columnCount} colonne(s).",
      viewStructuredJson: "Voir le JSON structuré",
      previewShortened:
        "Aperçu raccourci. Téléchargez le fichier complet pour voir toutes les lignes.",
      selectedFieldStatusTitle: "Statut des champs sélectionnés",
      selectedFieldStatusHelp:
        "Les champs demandés sont indiqués comme trouvés, non trouvés ou à faible confiance, avec la preuve disponible.",
      fieldStatusFound: "Trouvé",
      fieldStatusNotFound: "Non trouvé",
      fieldStatusLowConfidence: "Faible confiance",
      fieldStatusEvidence: "Preuve",
      fieldStatusNoEvidence: "Aucun extrait de preuve disponible",
      fieldStatusValue: "Valeur",
      extractionQuality: "Couverture de l’extraction",
      reviewNotes: "Notes de vérification",
      qualityHelp:
        "La couverture indique combien de champs demandés ont été trouvés. Comparez toujours les valeurs importantes avec la source.",
      notApplicable: "Non applicable",
    },

    outputFormatLabels: {
      json: ".json",
      csv: ".csv",
      xlsx: ".xlsx",
    },

    resultShapeLabels: {
      machine_readable: "JSON technique complet",
      key_value_fields: "Champs simples",
      tables: "Tableaux uniquement",
      row_based_records: "Lignes de tableur",
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
    badge: "Document compliance check",
    title: "Check what your document needs for compliance",
    description:
      "Choose a country and business sector. ReDOCX will explain what it found, what is missing, and what to do next.",
    uploadTitle: "Upload document(s) to check",
    allowedFileInputs:
      "Allowed inputs: .pdf, .docx, .jpg, .jpeg, .png. Upload 1 to {maxFiles} documents.",
    complianceOutput: "What ReDOCX found",
    previewText:
      "Your result will appear here in plain language, with clear next steps.",
    checkAction: "Check compliance",
    checking: "Checking",
    complianceLabel: "Rules selected for:",
    complianceCompleted: "Compliance check completed",

    unsupportedFileType:
      "Unsupported file type: {ext}. Only .pdf, .docx, .jpg, .jpeg, and .png are allowed",
    fileTooLarge: "File is too large, maximum allowed size is {maxSize} MB",
    chooseFileToCheck: "Please choose at least one file to check",
    compliancePotentialIssue: "Something went wrong while checking compliance",
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
    sectorPacksLabel: "Business sector",
    corePackHelp:
      "ReDOCX automatically includes the basic rules for {country}. Add the business sector that applies to this document.",
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

    inputFile: "Input file(s)",
    inputExtension: "Input extension",
    jurisdictionResult: "Country/jurisdiction",
    sectorPacksResult: "Rules used",
    regulatoryDomainsResult: "Focus areas",
    reportVariantResult: "Report format",
    outputFormatResult: "Output format",
    reportFile: "Report file",
    allDomains: "All available focus areas",
    outputReadyText: "Your compliance report is ready to download",
    humanReviewRequired:
      "A qualified person should confirm the final document before you rely on it.",
    downloadReady: "Download ready",
    chooseFiles: "Choose files",
    clearFiles: "Clear files",
    filesLabel: "files",
    filesAccepted: "{count} file(s) accepted",
    tooManyFiles:
      "Upload a maximum of {maxFiles} files for one compliance check.",
    inputFiles: "Input files",
    previewAction: "Preview compliance",
    previewing: "Previewing...",
    generateFileAction: "Check documents and create report",
    previewCompleted: "Compliance preview completed.",
    annotatedSourcePdf: "Annotated source PDF",
    evidenceOverlayReport: "Evidence overlay report",
    annotatedSourcePdfPackage: "Annotated source PDF package",
    evidenceOverlayReportPackage: "Evidence overlay report package",
    annotatedAndEvidencePackage:
      "Annotated source PDFs + evidence overlay report",
    annotatedSourceDynamicDescription:
      "Selected output: {output}. PDF files are annotated directly. DOCX and image files receive an evidence overlay report.",

    findingsSummary: "Check summary",
    evidenceFound: "Information found",
    riskDetected: "Needs attention",
    evidenceMissing: "Information missing",
    passed: "Information found",
    failed: "Needs attention",
    warning: "Check this",
    missing: "Information missing",
    reviewRequiredCount: "Person must review",
    reviewRequiredShort: "Review",
    overallResult: "Overall result",
    documentQualityNotes: "Document quality notes",
    ruleOptionsUnavailable:
      "Compliance rules are not available right now. Please contact your ReDOCX administrator instead of relying on an incomplete check.",
    whatToDoNext: "What to do next",
    checkResults: "Checks and fixes",
    whatToDo: "How to fix this",
    supportingEvidence: "Where this result came from",
    noSupportingEvidence:
      "ReDOCX did not find supporting text in the uploaded document.",
    documentLabel: "Document",
    pageLabel: "page",
    moreEvidenceLocations: "{count} more location(s) found.",
    ruleDetails: "Rule details (optional)",
    ruleReferenceLabel: "Rule reference",
    ruleVersionLabel: "Rule version",
    rulePackLabel: "Rule pack",
    overallStatusLabels: {
      ready_for_final_review: "Ready for final review",
      changes_recommended: "Changes recommended",
      manual_review_needed: "A person needs to review this",
    },
    ruleStatusLabels: {
      evidence_found: "Information found",
      risk_detected: "Possible problem",
      warning: "Check this",
      evidence_missing: "Information missing",
      requires_review: "Person must review",
    },

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
      zip: ".zip",
    },

    reportVariantLabels: {
      human_readable_report: "PDF report for review (recommended)",
      machine_readable_report: "JSON report for systems/API",
      annotated_source_output: "Annotated source PDF / Evidence overlay report",
    },

    reportVariantDescriptions: {
      human_readable_report:
        "Best for reading, sharing, and downloading a normal compliance report",
      machine_readable_report:
        "Best for developers, dashboards, databases, APIs, or automated workflows",
      annotated_source_output:
        "For PDF files, ReDOCX returns an Annotated source PDF. For DOCX/image files, ReDOCX returns an Evidence overlay report. For multiple files, ReDOCX returns a source-output package.",
    },

    sectorPackLabels: {
      nigeria_core_control_library: "Basic rules",
      core_control_library: "Basic rules",

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
    badge: "Vérification de conformité des documents",
    title: "Vérifier ce qu’il faut au document pour être conforme",
    description:
      "Choisissez un pays et un secteur. ReDOCX explique ce qui est présent, ce qui manque et les prochaines étapes.",
    uploadTitle: "Téléverser un ou plusieurs documents à vérifier",
    allowedFileInputs:
      "Entrées autorisées : .pdf, .docx, .jpg, .jpeg, .png. Téléversez 1 à {maxFiles} documents.",
    complianceOutput: "Ce que ReDOCX a trouvé",
    previewText:
      "Le résultat apparaîtra ici en langage simple, avec des étapes claires.",
    checkAction: "Vérifier la conformité",
    checking: "Vérification",
    complianceLabel: "Règles sélectionnées pour:",
    complianceCompleted: "Vérification de conformité terminée",

    unsupportedFileType:
      "Type de fichier non pris en charge: {ext}. Seuls les formats .pdf, .docx, .jpg, .jpeg et .png sont autorisés",
    fileTooLarge:
      "Le fichier est trop volumineux, la taille maximale autorisée est de {maxSize} Mo",
    chooseFileToCheck: "Veuillez choisir au moins un fichier à vérifier",
    compliancePotentialIssue:
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
    sectorPacksLabel: "Secteur d’activité",
    corePackHelp:
      "ReDOCX inclut automatiquement les règles de base pour {country}. Ajoutez le secteur d’activité concerné par ce document.",
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

    inputFile: "Fichier(s) d’entrée",
    inputExtension: "Extension d’entrée",
    jurisdictionResult: "Pays/Juridiction",
    sectorPacksResult: "Règles utilisées",
    regulatoryDomainsResult: "Domaines de vérification",
    reportVariantResult: "Format du rapport",
    outputFormatResult: "Format de sortie",
    reportFile: "Fichier du rapport",
    allDomains: "Tous les domaines disponibles",
    outputReadyText: "Votre rapport de conformité est prêt à être téléchargé",
    humanReviewRequired:
      "Une personne qualifiée doit confirmer le document final avant son utilisation.",
    downloadReady: "Téléchargement prêt",
    chooseFiles: "Choisir des fichiers",
    clearFiles: "Effacer les fichiers",
    filesLabel: "fichiers",
    filesAccepted: "{count} fichier(s) accepté(s)",
    tooManyFiles:
      "Téléversez au maximum {maxFiles} fichiers pour une vérification de conformité.",
    inputFiles: "Fichiers d’entrée",
    previewAction: "Prévisualiser la conformité",
    previewing: "Prévisualisation...",
    generateFileAction: "Vérifier les documents et créer le rapport",
    previewCompleted: "Aperçu de conformité terminé.",
    annotatedSourcePdf: "PDF source annoté",
    evidenceOverlayReport: "Rapport de preuves",
    annotatedSourcePdfPackage: "Package de PDF sources annotés",
    evidenceOverlayReportPackage: "Package de rapports de preuves",
    annotatedAndEvidencePackage: "PDF sources annotés + rapport de preuves",
    annotatedSourceDynamicDescription:
      "Sortie sélectionnée : {output}. Les PDF sont annotés directement. Les fichiers DOCX et image reçoivent un rapport de preuves.",

    findingsSummary: "Résumé de la vérification",
    evidenceFound: "Information trouvée",
    riskDetected: "À corriger",
    evidenceMissing: "Information manquante",
    passed: "Information trouvée",
    failed: "À corriger",
    warning: "À vérifier",
    missing: "Information manquante",
    reviewRequiredCount: "Une personne doit vérifier",
    reviewRequiredShort: "Révision",
    overallResult: "Résultat global",
    documentQualityNotes: "Notes sur la qualité des documents",
    ruleOptionsUnavailable:
      "Les règles de conformité ne sont pas disponibles actuellement. Contactez votre administrateur ReDOCX au lieu de vous fier à une vérification incomplète.",
    whatToDoNext: "Prochaines étapes",
    checkResults: "Vérifications et corrections",
    whatToDo: "Comment corriger",
    supportingEvidence: "Origine de ce résultat",
    noSupportingEvidence:
      "ReDOCX n’a pas trouvé de texte justificatif dans le document téléversé.",
    documentLabel: "Document",
    pageLabel: "page",
    moreEvidenceLocations: "{count} autre(s) emplacement(s) trouvé(s).",
    ruleDetails: "Détails de la règle (facultatif)",
    ruleReferenceLabel: "Référence de la règle",
    ruleVersionLabel: "Version de la règle",
    rulePackLabel: "Pack de règles",
    overallStatusLabels: {
      ready_for_final_review: "Prêt pour la validation finale",
      changes_recommended: "Modifications recommandées",
      manual_review_needed: "Une personne doit vérifier ce point",
    },
    ruleStatusLabels: {
      evidence_found: "Information trouvée",
      risk_detected: "Problème possible",
      warning: "À vérifier",
      evidence_missing: "Information manquante",
      requires_review: "Une personne doit vérifier",
    },

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
      zip: ".zip",
    },

    reportVariantLabels: {
      human_readable_report: "Rapport PDF pour révision (recommandé)",
      machine_readable_report: "Rapport JSON pour systèmes/API",
      annotated_source_output: "PDF source annoté / Rapport de preuves",
    },

    reportVariantDescriptions: {
      human_readable_report:
        "Idéal pour lire, partager et télécharger un rapport de conformité normal",
      machine_readable_report:
        "Idéal pour les développeurs, tableaux de bord, bases de données, API ou workflows automatisés",
      annotated_source_output:
        "Pour les fichiers PDF, ReDOCX renvoie un PDF source annoté. Pour les fichiers DOCX/image, ReDOCX renvoie un rapport de preuves. Pour plusieurs fichiers, ReDOCX renvoie un package de sorties source.",
    },

    sectorPackLabels: {
      nigeria_core_control_library: "Règles de base",
      core_control_library: "Règles de base",

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
    title: "Team Settings",
    subtitle:
      "Manage your organization, subscription seats, members, roles, and invitations.",
    loading: "Loading team...",
    noTeam: "No team workspace found.",
    refresh: "Refresh",
    organization: "Organization",
    organizationName: "Organization name",
    edit: "Edit",
    save: "Save",
    saving: "Saving...",
    cancel: "Cancel",
    organizationRenamed: "Organization name updated.",
    organizationNameTooShort:
      "Organization name must contain at least 2 characters.",
    organizationNameTooLong: "Organization name cannot exceed 100 characters.",
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
    title: "Paramètres de l’équipe",
    subtitle:
      "Gérez votre organisation, les sièges d’abonnement, les membres, les rôles et les invitations.",
    loading: "Chargement de l’équipe...",
    noTeam: "Aucun espace d’équipe trouvé.",
    refresh: "Actualiser",
    organization: "Organisation",
    organizationName: "Nom de l’organisation",
    edit: "Modifier",
    save: "Enregistrer",
    saving: "Enregistrement...",
    cancel: "Annuler",
    organizationRenamed: "Le nom de l’organisation a été mis à jour.",
    organizationNameTooShort:
      "Le nom de l’organisation doit comporter au moins 2 caractères.",
    organizationNameTooLong:
      "Le nom de l’organisation ne peut pas dépasser 100 caractères.",
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
      "Guests can use one PDF Tools request before signing in. Authenticated Free accounts receive 4 PDF Tools requests every 4 hours. Personal, Business, and Enterprise users retain their existing paid-plan access.",
    signInTitle: "Sign in required",
    signInDescription:
      "After the one-time guest PDF Tools request is used, sign in to continue.",
    signIn: "Sign in",
    loading: "Checking account...",
    freeQuota: "Guest & Free access",
    paidUnlimited: "Paid plans: unlimited",
    quotaDescription:
      "Guests receive one PDF Tools request. Authenticated Free accounts receive 4 requests in each rolling 4-hour window. Paid plans retain the existing paid-plan safety guards.",
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
        description:
          "Add text, highlights, drawings, images, whiteouts, and signatures.",
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
      "Les visiteurs disposent d’une requête Outils PDF avant connexion. Les comptes Free authentifiés disposent de 4 requêtes Outils PDF toutes les 4 heures. Les utilisateurs Personal, Business et Enterprise conservent leur accès payant existant.",
    signInTitle: "Connexion requise",
    signInDescription:
      "Après l’utilisation de la requête Outils PDF gratuite en mode visiteur, connectez-vous pour continuer.",
    signIn: "Se connecter",
    loading: "Vérification du compte...",
    freeQuota: "Accès visiteur et Free",
    paidUnlimited: "Forfaits payants : illimité",
    quotaDescription:
      "Les visiteurs disposent d’une requête Outils PDF. Les comptes Free authentifiés disposent de 4 requêtes par fenêtre glissante de 4 heures. Les forfaits payants conservent leurs contrôles de sécurité existants.",
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
        description:
          "Réduisez la taille du PDF tout en préservant le contenu visible.",
      },
      {
        key: "editPdf",
        name: "Modifier PDF",
        route: "/pdf-tools/edit",
        description:
          "Ajoutez texte, surlignages, dessins, images, masques blancs et signatures.",
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
  en: {
    back: "Back",
    badge: "PDF tools",
    title: "Combine PDF files",
    description:
      "Merge up to 10 PDFs in the exact order shown. Existing typed, drawn, and uploaded-image signatures are preserved as PDF content.",
    uploadTitle: "PDF files",
    uploadHelp: "Allowed: .pdf only. Each file can be up to 50 MB.",
    chooseFiles: "Choose PDFs",
    outputFilename: "Output filename",
    preserveBookmarks: "Preserve bookmarks",
    preserveMetadata: "Preserve metadata",
    combine: "Combine PDFs",
    combining: "Combining...",
    resultTitle: "Combined PDF ready",
    download: "Download combined PDF",
    signInTitle: "Sign in required",
    signInDescription: "PDF tools use your authenticated analyzer quota.",
    signIn: "Sign in",
    loading: "Checking account...",
    noFiles: "Choose at least two PDF files.",
    invalidFile: "Only PDF files are supported.",
    tooMany: "You can combine at most 10 files.",
    tooLarge: "Each PDF must be 50 MB or smaller.",
    failed: "Could not combine PDFs.",
  },
  fr: {
    back: "Retour",
    badge: "Outils PDF",
    title: "Combiner des PDF",
    description:
      "Fusionnez jusqu’à 10 PDF dans l’ordre affiché. Les signatures typées, dessinées et image déjà présentes sont préservées comme contenu PDF.",
    uploadTitle: "Fichiers PDF",
    uploadHelp: "Autorisé : .pdf uniquement. 50 Mo maximum par fichier.",
    chooseFiles: "Choisir des PDF",
    outputFilename: "Nom du fichier de sortie",
    preserveBookmarks: "Préserver les signets",
    preserveMetadata: "Préserver les métadonnées",
    combine: "Combiner",
    combining: "Combinaison...",
    resultTitle: "PDF combiné prêt",
    download: "Télécharger le PDF",
    signInTitle: "Connexion requise",
    signInDescription: "Les outils PDF utilisent votre quota authentifié.",
    signIn: "Se connecter",
    loading: "Vérification du compte...",
    noFiles: "Choisissez au moins deux PDF.",
    invalidFile: "Seuls les PDF sont pris en charge.",
    tooMany: "Vous pouvez combiner au plus 10 fichiers.",
    tooLarge: "Chaque PDF doit faire au plus 50 Mo.",
    failed: "Impossible de combiner les PDF.",
  },
};

export const compressPdfPageTranslations = {
  en: {
    back: "Back",
    badge: "PDF tools",
    title: "Compress PDF files",
    description:
      "Compress PDFs while preserving visible PDF content, including existing typed, drawn, and uploaded-image signatures. New signature placement belongs in E-signature or Edit PDF.",
    uploadTitle: "Source PDF",
    uploadHelp: "Allowed: .pdf only. Maximum 100 MB.",
    chooseFile: "Choose PDF",
    compressionLevel: "Compression level",
    smallFile: "Small file",
    balanced: "Balanced",
    highQuality: "High quality",
    outputFilename: "Output filename",
    asyncProcessing: "Allow async processing",
    compress: "Compress PDF",
    compressing: "Compressing...",
    resultTitle: "Compressed PDF ready",
    download: "Download compressed PDF",
    signInTitle: "Sign in required",
    signInDescription: "PDF tools use your authenticated analyzer quota.",
    signIn: "Sign in",
    loading: "Checking account...",
    noFile: "Choose a PDF file.",
    invalidFile: "Only PDF files are supported.",
    tooLarge: "The PDF must be 50 MB or smaller.",
    failed: "Could not compress PDF.",
  },
  fr: {
    back: "Retour",
    badge: "Outils PDF",
    title: "Compresser des PDF",
    description:
      "Compressez des PDF tout en préservant le contenu visible, y compris les signatures typées, dessinées et image déjà présentes. L’ajout de signatures se fait dans E-signature ou Modifier PDF.",
    uploadTitle: "PDF source",
    uploadHelp: "Autorisé : .pdf uniquement. Maximum 50 Mo.",
    chooseFile: "Choisir un PDF",
    compressionLevel: "Niveau de compression",
    smallFile: "Petit fichier",
    balanced: "Équilibré",
    highQuality: "Haute qualité",
    outputFilename: "Nom du fichier de sortie",
    asyncProcessing: "Autoriser le traitement asynchrone",
    compress: "Compresser",
    compressing: "Compression...",
    resultTitle: "PDF compressé prêt",
    download: "Télécharger",
    signInTitle: "Connexion requise",
    signInDescription: "Les outils PDF utilisent votre quota authentifié.",
    signIn: "Se connecter",
    loading: "Vérification du compte...",
    noFile: "Choisissez un PDF.",
    invalidFile: "Seuls les PDF sont pris en charge.",
    tooLarge: "Le PDF doit faire 50 Mo ou moins.",
    failed: "Impossible de compresser le PDF.",
  },
};

export const editPdfPageTranslations = {
  en: {
    back: "Back",
    badge: "PDF tools",
    title: "Edit your PDF",
    description:
      "Add or remove text and images, draw, highlight, white out content, and place visual signatures.",
    uploadTitle: "Source PDF",
    uploadHelp: "Allowed: .pdf only. Maximum 100 MB.",
    chooseFile: "Choose PDF",
    fileReady: "PDF selected and ready",
    removeFile: "Remove PDF",
    outputSettings: "Output settings",
    operationBuilder: "Operation builder",
    operationBuilderHelp:
      "Choose one edit, select its page and area, then add it to the list. You can add and remove operations before applying them.",
    operationType: "Operation type",
    addText: "Add text",
    removeText: "Remove text",
    addImage: "Add image",
    removeImage: "Remove image",
    draw: "Draw",
    highlight: "Highlight",
    whiteout: "Whiteout",
    addSignature: "Add signature",
    removeSignature: "Remove signature",
    add_textHelp: "Place new text inside the selected area.",
    remove_textHelp:
      "Permanently remove text content inside the selected area.",
    add_imageHelp:
      "Choose a PNG or JPEG and place it inside the selected area.",
    remove_imageHelp:
      "Permanently remove image content inside the selected area.",
    drawHelp:
      "Draw naturally in the pad; the drawing is fitted into the selected area.",
    highlightHelp:
      "Add a real PDF highlight annotation over the selected area.",
    whiteoutHelp:
      "Permanently remove all content in the selected area and replace it with white.",
    add_signatureHelp: "Add a typed, hand-drawn, or uploaded visual signature.",
    remove_signatureHelp:
      "Remove signature fields, annotations, and flattened signature content in the selected area.",
    pageNumber: "Page number",
    positionPreset: "Quick position",
    top: "Top",
    center: "Center",
    bottom: "Bottom",
    custom: "Custom",
    positionAndSize: "Position and size",
    rectangleHelp:
      "Values are percentages of the PDF page. Left and top set the starting point; width and height set the size.",
    leftPercent: "Left (%)",
    topPercent: "Top (%)",
    widthPercent: "Width (%)",
    heightPercent: "Height (%)",
    rectangle: "Normalized rectangle",
    text: "Text",
    fontSize: "Font size",
    color: "Color",
    opacity: "Opacity",
    svgPath: "SVG path",
    strokeWidth: "Stroke width",
    drawPad: "Drawing pad",
    drawPadHelp: "Use a mouse, trackpad, stylus, or finger to draw.",
    clearDrawing: "Clear drawing",
    imageStorageKey: "Image storage key",
    imageMimeType: "Image MIME type",
    chooseImage: "Choose image",
    selectedImage: "Selected image",
    imageHelp: "PNG or JPEG only, maximum 10 MB.",
    signatureType: "Signature type",
    typed: "Typed",
    drawn: "Drawn",
    uploaded: "Uploaded image",
    typedName: "Typed signature name",
    signatureSvgStorageKey: "Signature SVG storage key",
    signatureImageStorageKey: "Signature image storage key",
    signaturePad: "Signature pad",
    signaturePadHelp:
      "Sign inside the white area using a mouse, trackpad, stylus, or finger.",
    clearSignature: "Clear signature",
    chooseSignatureImage: "Choose signature image",
    signatureConsent:
      "I confirm that I am authorized to place this signature on the document.",
    signatureNotice:
      "This places a visual signature. Use the dedicated e-signature workflow when signer identity, delivery, or an audit trail is required.",
    permanentRemovalWarning:
      "This action permanently removes content inside the selected area. Check the page and position carefully before applying edits.",
    assetKeyHelp:
      "Drawn/uploaded signatures use storage keys because the backend resolves assets by key/path.",
    addOperation: "Add operation",
    addOperationHelp:
      "This saves the operation to the list; it does not edit the PDF yet.",
    operations: "Operations",
    operationsHelp:
      "Review the edit order and remove anything you do not want before applying edits.",
    removeOperation: "Remove operation",
    noOperations: "No operations added yet.",
    outputFilename: "Output filename",
    generatePreview: "Generate preview PDF",
    generatePreviewHelp:
      "Creates a preview copy and displays it after the edits finish.",
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
    previewTitle: "Edited PDF preview",
    noFile: "Choose a PDF file.",
    invalidFile: "Only PDF files are supported.",
    tooLarge: "The PDF must be 100 MB or smaller.",
    badRectangle:
      "Position and size must be valid percentages that stay inside the page.",
    badPage: "Enter a valid page number starting from 1.",
    badFontSize: "Font size must be between 4 and 144.",
    badStrokeWidth: "Stroke width must be between 0.25 and 25.",
    needsText: "This operation requires text.",
    needsPath: "Draw operation requires an SVG path.",
    needsDrawing: "Draw something in the drawing pad first.",
    needsImageKey: "Image operation requires an image storage key.",
    needsImage: "Choose a PNG or JPEG image first.",
    needsSignature: "Complete the selected signature source.",
    needsConsent: "Confirm that you are authorized to place this signature.",
    failed: "Could not edit PDF.",
  },
  fr: {
    back: "Retour",
    badge: "Outils PDF",
    title: "Modifier votre PDF",
    description:
      "Ajoutez ou supprimez du texte et des images, dessinez, surlignez, masquez du contenu et placez des signatures visuelles.",
    uploadTitle: "PDF source",
    uploadHelp: "Autorisé : .pdf uniquement. Maximum 100 Mo.",
    chooseFile: "Choisir un PDF",
    fileReady: "PDF sélectionné et prêt",
    removeFile: "Supprimer le PDF",
    outputSettings: "Paramètres de sortie",
    operationBuilder: "Constructeur d’opération",
    operationBuilderHelp:
      "Choisissez une modification, sa page et sa zone, puis ajoutez-la à la liste. Vous pouvez ajouter ou supprimer des choix avant de les appliquer.",
    operationType: "Type d’opération",
    addText: "Ajouter du texte",
    removeText: "Supprimer du texte",
    addImage: "Ajouter une image",
    removeImage: "Supprimer une image",
    draw: "Dessiner",
    highlight: "Surligner",
    whiteout: "Masquer en blanc",
    addSignature: "Ajouter une signature",
    removeSignature: "Supprimer une signature",
    add_textHelp: "Placez un nouveau texte dans la zone sélectionnée.",
    remove_textHelp:
      "Supprimez définitivement le texte dans la zone sélectionnée.",
    add_imageHelp:
      "Choisissez une image PNG ou JPEG à placer dans la zone sélectionnée.",
    remove_imageHelp:
      "Supprimez définitivement l’image dans la zone sélectionnée.",
    drawHelp:
      "Dessinez dans la zone prévue ; le dessin sera ajusté à la zone sélectionnée.",
    highlightHelp: "Ajoutez une véritable annotation de surlignage PDF.",
    whiteoutHelp:
      "Supprimez définitivement tout le contenu de la zone et remplacez-le par du blanc.",
    add_signatureHelp:
      "Ajoutez une signature visuelle typée, dessinée ou importée.",
    remove_signatureHelp:
      "Supprimez les champs, annotations et contenus de signature dans la zone sélectionnée.",
    pageNumber: "Page",
    fieldValue: "Field value",
    positionPreset: "Position rapide",
    top: "Haut",
    center: "Centre",
    bottom: "Bas",
    custom: "Personnalisée",
    positionAndSize: "Position et dimensions",
    rectangleHelp:
      "Les valeurs sont des pourcentages de la page. Gauche et haut définissent le départ ; largeur et hauteur définissent la taille.",
    leftPercent: "Gauche (%)",
    topPercent: "Haut (%)",
    widthPercent: "Largeur (%)",
    heightPercent: "Hauteur (%)",
    rectangle: "Rectangle normalisé",
    text: "Texte",
    fontSize: "Taille police",
    color: "Couleur",
    opacity: "Opacité",
    svgPath: "Chemin SVG",
    strokeWidth: "Épaisseur",
    drawPad: "Zone de dessin",
    drawPadHelp:
      "Dessinez avec une souris, un pavé tactile, un stylet ou un doigt.",
    clearDrawing: "Effacer le dessin",
    imageStorageKey: "Clé de stockage image",
    imageMimeType: "MIME image",
    chooseImage: "Choisir une image",
    selectedImage: "Image sélectionnée",
    imageHelp: "PNG ou JPEG uniquement, maximum 10 Mo.",
    signatureType: "Type de signature",
    typed: "Typée",
    drawn: "Dessinée",
    uploaded: "Image téléversée",
    typedName: "Nom de signature typée",
    signatureSvgStorageKey: "Clé SVG de signature",
    signatureImageStorageKey: "Clé image de signature",
    signaturePad: "Zone de signature",
    signaturePadHelp:
      "Signez dans la zone blanche avec une souris, un pavé tactile, un stylet ou un doigt.",
    clearSignature: "Effacer la signature",
    chooseSignatureImage: "Choisir une image de signature",
    signatureConsent:
      "Je confirme être autorisé(e) à apposer cette signature sur le document.",
    signatureNotice:
      "Cette option place une signature visuelle. Utilisez le flux de signature électronique pour l’identité, l’envoi ou la piste d’audit.",
    permanentRemovalWarning:
      "Cette action supprime définitivement le contenu de la zone sélectionnée. Vérifiez soigneusement la page et la position.",
    assetKeyHelp:
      "Les signatures dessinées/téléversées utilisent des clés car le backend résout les assets par clé/chemin.",
    addOperation: "Ajouter l’opération",
    addOperationHelp:
      "L’opération est ajoutée à la liste ; le PDF n’est pas encore modifié.",
    operations: "Opérations",
    operationsHelp:
      "Vérifiez l’ordre et supprimez les opérations inutiles avant d’appliquer les modifications.",
    removeOperation: "Supprimer l’opération",
    noOperations: "Aucune opération ajoutée.",
    outputFilename: "Nom du fichier de sortie",
    generatePreview: "Générer un aperçu PDF",
    generatePreviewHelp:
      "Crée une copie d’aperçu et l’affiche après la modification.",
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
    previewTitle: "Aperçu du PDF modifié",
    noFile: "Choisissez un PDF.",
    invalidFile: "Seuls les PDF sont pris en charge.",
    tooLarge: "Le PDF doit faire 100 Mo ou moins.",
    badRectangle:
      "La position et les dimensions doivent être des pourcentages valides qui restent dans la page.",
    badPage: "Saisissez un numéro de page valide à partir de 1.",
    badFontSize: "La taille de police doit être comprise entre 4 et 144.",
    badStrokeWidth: "L’épaisseur doit être comprise entre 0,25 et 25.",
    needsText: "Cette opération nécessite du texte.",
    needsPath: "Le dessin nécessite un chemin SVG.",
    needsDrawing: "Dessinez d’abord dans la zone de dessin.",
    needsImageKey: "L’image nécessite une clé de stockage.",
    needsImage: "Choisissez d’abord une image PNG ou JPEG.",
    needsSignature: "Complétez la source de signature choisie.",
    needsConsent:
      "Confirmez que vous êtes autorisé(e) à apposer cette signature.",
    failed: "Impossible de modifier le PDF.",
  },
};

export const esignaturePageTranslations = {
  en: {
    back: "Back",
    badge: "ReDOCX Sign",
    title: "Sign documents or send them for signature",
    description:
      "Upload your PDF, choose who needs to sign, place the required fields, and ReDOCX handles the secure signing links and audit trail.",
    uploadTitle: "1. Choose your documents",
    uploadHelp: "Allowed: up to 20 PDF documents per envelope, 100 MB each.",
    chooseFile: "Choose PDFs",
    selectedDocuments: "{count} document(s) selected",
    maxEnvelopeDocuments: "An envelope supports at most 20 documents.",
    fieldDocumentMissing: "Every field must belong to a document in this envelope.",
    documentFieldCount: "{count} field(s)",
    workflow: "2. Who needs to sign?",
    selfSign: "Only me",
    sendSingle: "One other person",
    sendMultiple: "Several people",
    selfSignThenSend: "Me first, then others",
    routingMode: "How should multiple people sign?",
    sequential: "One after another",
    parallel: "In any order",
    routingHelpSequential: "Each person receives the document after the previous signer finishes.",
    routingHelpParallel: "All recipients receive their signing links at the same time.",
    signerDetails: "Your details",
    signerName: "Signer name",
    signerEmail: "Signer email",
    recipients: "Recipients",
    recipientName: "Recipient name",
    recipientEmail: "Recipient email",
    signingOrder: "Signing order",
    addRecipient: "Add recipient",
    fields: "Signing fields",
    addField: "Add a field",
    fieldValue: "Value",
    visualDesigner: "3. Place the signing fields",
    visualDesignerHelp:
      "Drag each field to the right place on the PDF. ReDOCX checks that fields do not cover document content before you continue.",
    designerEmpty: "Choose a PDF to open the visual field designer.",
    previewPage: "PDF preview page",
    previousPage: "Previous page",
    nextPage: "Next page",
    pageOf: "Page {page} of {count}",
    refreshPreview: "Check field placement",
    findSignatureLines: "Suggest signature locations",
    importNativeFields: "Import {count} PDF form field(s)",
    addSignaturePage: "Add dedicated signature page",
    addSignaturePageHelp:
      "Append signature space instead of changing or covering the original document layout.",
    collisionSummary: "{count} field placement(s) overlap existing content or another field.",
    selectedAreaCollision: "This selected area is not safe for signing.",
    selectedAreaClear: "The selected field is clear of detected document content.",
    textUnderSelection: "Text under selection",
    fieldOverlap: "This field overlaps another signing field.",
    signatureSuggestions: "Suggested signature locations",
    signatureSuggestion: "Signature location suggestion",
    senderConfirmationRequired: "Review this location before adding it.",
    addSuggestion: "Add field",
    resizeField: "Resize field",
    layoutFailed: "Could not analyze the PDF layout.",
    collisionBlocking:
      "One or more signing fields overlap existing PDF content or another field. Move them to clear space or add a signature page before sending.",
    fieldType: "What should be entered?",
    assignedTo: "Who fills this field?",
    pageNumber: "Page",
    rectangle: "Exact position",
    advancedPlacement: "Advanced placement settings",
    fieldLabel: "Field label",
    fieldTypeSignature: "Signature",
    fieldTypeInitials: "Initials",
    fieldTypeDate: "Date signed",
    fieldTypeName: "Full name",
    fieldTypeEmail: "Email address",
    fieldTypeText: "Text",
    fieldTypeCheckbox: "Checkbox",
    checkedByDefault: "Checked when I sign",
    x: "X",
    y: "Y",
    width: "Width",
    height: "Height",
    emailSubject: "Email subject",
    emailMessage: "Message to signers",
    expiresInDays: "Signing link expires after (days)",
    sendEmails: "ReDOCX will email each signer a secure signing link.",
    emailOptions: "Email options",
    emailOptionsHelp: "Optional: customize the invitation message and link expiry.",
    signatureSource: "Choose how your signature will look",
    typed: "Typed",
    drawn: "Drawn",
    uploaded: "Uploaded image",
    typedName: "Typed signature name",
    drawHere: "Draw signature here",
    clearDrawing: "Clear drawing",
    drawingCaptured: "Signature drawing captured securely.",
    uploadedPreview: "Uploaded image preview",
    submit: "Continue",
    signDocument: "Sign document",
    sendForSignature: "Send for signature",
    submitting: "Processing securely...",
    signInTitle: "Sign in required",
    signInDescription:
      "E-signature uses your authenticated analyzer quota and audit context.",
    signIn: "Sign in",
    loading: "Checking account...",
    resultTitle: "Done",
    envelopeId: "Reference",
    status: "Status",
    statusCompleted: "Signing completed",
    statusSent: "Sent for signature",
    statusPartial: "Waiting for remaining signers",
    statusDraft: "Draft saved",
    successSigned: "Your signed document is ready.",
    successSent: "Secure signing invitations have been sent.",
    downloadSigned: "Download signed PDF",
    downloadSignedEnvelope: "Download signed envelope",
    downloadCertificate: "Download certificate",
    openPreview: "Open preview",
    noFile: "Choose a PDF file.",
    invalidFile: "Only PDF files are supported.",
    tooLarge: "The PDF must be 100 MB or smaller.",
    badEmail: "Enter a valid email address.",
    badName: "Name is required.",
    badRecipient: "Recipient details are incomplete.",
    duplicateSignerEmail: "Each signer must use a different email address.",
    singleRecipientRequired: "Add one recipient for this workflow.",
    multipleRecipientsRequired: "Add at least two recipients for this workflow.",
    signerFieldRequired: "Add a signature or initials field for {signer}.",
    ownerTextRequired: "Enter a value for each required text field assigned to you.",
    ownerCheckboxRequired: "For each required checkbox assigned to you, turn on ‘Checked when I sign’.",
    badRectangle:
      "Rectangle values must be normalized and stay inside the page.",
    badSignature: "Complete the selected signature source.",
    failed: "Could not process the e-signature request.",
  },
  fr: {
    back: "Retour",
    badge: "ReDOCX Sign",
    title: "Signer des documents ou les envoyer pour signature",
    description:
      "Téléversez votre PDF, choisissez les signataires, placez les champs nécessaires et ReDOCX gère les liens sécurisés et la piste d’audit.",
    uploadTitle: "1. Choisissez vos documents",
    uploadHelp: "Autorisé : jusqu’à 20 documents PDF par enveloppe, 100 Mo chacun.",
    chooseFile: "Choisir des PDF",
    selectedDocuments: "{count} document(s) sélectionné(s)",
    maxEnvelopeDocuments: "Une enveloppe peut contenir au maximum 20 documents.",
    fieldDocumentMissing: "Chaque champ doit appartenir à un document de cette enveloppe.",
    documentFieldCount: "{count} champ(s)",
    workflow: "2. Qui doit signer ?",
    selfSign: "Moi uniquement",
    sendSingle: "Une autre personne",
    sendMultiple: "Plusieurs personnes",
    selfSignThenSend: "Moi d’abord, puis les autres",
    routingMode: "Comment plusieurs personnes doivent-elles signer ?",
    sequential: "L’une après l’autre",
    parallel: "Dans n’importe quel ordre",
    routingHelpSequential: "Chaque personne reçoit le document après la signature de la personne précédente.",
    routingHelpParallel: "Tous les destinataires reçoivent leur lien de signature en même temps.",
    signerDetails: "Vos informations",
    signerName: "Nom du signataire",
    signerEmail: "Email du signataire",
    recipients: "Destinataires",
    recipientName: "Nom du destinataire",
    recipientEmail: "Email du destinataire",
    signingOrder: "Ordre",
    addRecipient: "Ajouter un destinataire",
    fields: "Champs de signature",
    addField: "Ajouter un champ",
    visualDesigner: "3. Placez les champs de signature",
    visualDesignerHelp:
      "Déplacez chaque champ au bon endroit sur le PDF. ReDOCX vérifie qu’aucun champ ne recouvre le contenu du document.",
    designerEmpty: "Choisissez un PDF pour ouvrir le concepteur visuel.",
    previewPage: "Page d’aperçu PDF",
    previousPage: "Page précédente",
    nextPage: "Page suivante",
    pageOf: "Page {page} sur {count}",
    refreshPreview: "Vérifier le placement",
    findSignatureLines: "Suggérer des emplacements de signature",
    importNativeFields: "Importer {count} champ(s) PDF natif(s)",
    addSignaturePage: "Ajouter une page de signature dédiée",
    addSignaturePageHelp:
      "Ajoutez un espace de signature sans modifier ni recouvrir la mise en page du document original.",
    collisionSummary: "{count} placement(s) chevauchent du contenu existant ou un autre champ.",
    selectedAreaCollision: "La zone sélectionnée n’est pas sûre pour la signature.",
    selectedAreaClear: "Le champ sélectionné ne chevauche aucun contenu détecté.",
    textUnderSelection: "Texte sous la sélection",
    fieldOverlap: "Ce champ chevauche un autre champ de signature.",
    signatureSuggestions: "Emplacements de signature suggérés",
    signatureSuggestion: "Suggestion d’emplacement de signature",
    senderConfirmationRequired: "Vérifiez cet emplacement avant de l’ajouter.",
    addSuggestion: "Ajouter le champ",
    resizeField: "Redimensionner le champ",
    layoutFailed: "Impossible d’analyser la mise en page du PDF.",
    collisionBlocking:
      "Un ou plusieurs champs chevauchent du contenu PDF existant ou un autre champ. Déplacez-les vers une zone libre ou ajoutez une page de signature avant l’envoi.",
    fieldType: "Que faut-il saisir ?",
    assignedTo: "Qui remplit ce champ ?",
    pageNumber: "Page",
    fieldValue: "Valeur",
    rectangle: "Position exacte",
    advancedPlacement: "Paramètres de placement avancés",
    fieldLabel: "Libellé du champ",
    fieldTypeSignature: "Signature",
    fieldTypeInitials: "Initiales",
    fieldTypeDate: "Date de signature",
    fieldTypeName: "Nom complet",
    fieldTypeEmail: "Adresse e-mail",
    fieldTypeText: "Texte",
    fieldTypeCheckbox: "Case à cocher",
    checkedByDefault: "Cochée lorsque je signe",
    x: "X",
    y: "Y",
    width: "Largeur",
    height: "Hauteur",
    emailSubject: "Objet de l’e-mail",
    emailMessage: "Message aux signataires",
    expiresInDays: "Le lien de signature expire après (jours)",
    sendEmails: "ReDOCX enverra à chaque signataire un lien de signature sécurisé.",
    emailOptions: "Options de l’e-mail",
    emailOptionsHelp: "Facultatif : personnalisez le message d’invitation et la durée du lien.",
    signatureSource: "Choisissez l’apparence de votre signature",
    typed: "Typée",
    drawn: "Dessinée",
    uploaded: "Image téléversée",
    typedName: "Nom de signature typée",
    drawHere: "Dessinez ici",
    clearDrawing: "Effacer",
    drawingCaptured: "Le dessin de la signature a été capturé en toute sécurité.",
    uploadedPreview: "Aperçu de l’image",
    submit: "Continuer",
    signDocument: "Signer le document",
    sendForSignature: "Envoyer pour signature",
    submitting: "Traitement sécurisé...",
    signInTitle: "Connexion requise",
    signInDescription:
      "La signature utilise votre quota authentifié et le contexte d’audit.",
    signIn: "Se connecter",
    loading: "Vérification du compte...",
    resultTitle: "Terminé",
    envelopeId: "Référence",
    status: "Statut",
    statusCompleted: "Signature terminée",
    statusSent: "Envoyé pour signature",
    statusPartial: "En attente des autres signataires",
    statusDraft: "Brouillon enregistré",
    successSigned: "Votre document signé est prêt.",
    successSent: "Les invitations de signature sécurisées ont été envoyées.",
    downloadSigned: "Télécharger le PDF signé",
    downloadSignedEnvelope: "Télécharger l’enveloppe signée",
    downloadCertificate: "Télécharger le certificat",
    openPreview: "Ouvrir l’aperçu",
    noFile: "Choisissez un PDF.",
    invalidFile: "Seuls les PDF sont pris en charge.",
    tooLarge: "Le PDF doit faire 100 Mo ou moins.",
    badEmail: "Saisissez un email valide.",
    badName: "Le nom est requis.",
    badRecipient: "Les informations du destinataire sont incomplètes.",
    duplicateSignerEmail: "Chaque signataire doit utiliser une adresse email différente.",
    singleRecipientRequired: "Ajoutez un destinataire pour ce flux.",
    multipleRecipientsRequired: "Ajoutez au moins deux destinataires pour ce flux.",
    signerFieldRequired: "Ajoutez un champ de signature ou de paraphe pour {signer}.",
    ownerTextRequired: "Saisissez une valeur pour chaque champ texte obligatoire qui vous est attribué.",
    ownerCheckboxRequired: "Pour chaque case obligatoire qui vous est attribuée, activez « Cochée lorsque je signe ».",
    badRectangle: "Le rectangle doit être normalisé et rester dans la page.",
    badSignature: "Complétez la source de signature choisie.",
    failed: "Impossible de traiter la demande de signature.",
  },
};

export const splitPdfPageTranslations = {
  en: {
    back: "Back",
    badge: "PDF tools",
    title: "Split PDF files",
    description:
      "Split a PDF by every page, selected pages, or page ranges. Signature appearances already present in extracted pages are preserved as PDF content.",
    uploadTitle: "Source PDF",
    uploadHelp: "Allowed: .pdf only. Maximum 50 MB.",
    chooseFile: "Choose PDF",
    mode: "Split mode",
    everyPage: "Every page",
    selectedPages: "Selected pages",
    pageRanges: "Page ranges",
    selectedPagesInput: "Selected pages, e.g. 1,3,5",
    pageRangesInput: "Page ranges, e.g. 1-3,5-7",
    outputBasename: "Output basename",
    split: "Split PDF",
    splitting: "Splitting...",
    resultTitle: "Split result ready",
    downloadArchive: "Download ZIP archive",
    downloadFile: "Download file",
    signInTitle: "Sign in required",
    signInDescription: "PDF tools use your authenticated analyzer quota.",
    signIn: "Sign in",
    loading: "Checking account...",
    noFile: "Choose a PDF file.",
    invalidFile: "Only PDF files are supported.",
    tooLarge: "The PDF must be 50 MB or smaller.",
    badSelected: "Selected pages are required for this mode.",
    badRanges: "Page ranges are required for this mode.",
    failed: "Could not split PDF.",
  },
  fr: {
    back: "Retour",
    badge: "Outils PDF",
    title: "Diviser des PDF",
    description:
      "Divisez un PDF par page, pages choisies ou plages. Les signatures déjà présentes sont préservées comme contenu PDF.",
    uploadTitle: "PDF source",
    uploadHelp: "Autorisé : .pdf uniquement. Maximum 50 Mo.",
    chooseFile: "Choisir un PDF",
    mode: "Mode de division",
    everyPage: "Chaque page",
    selectedPages: "Pages sélectionnées",
    pageRanges: "Plages",
    selectedPagesInput: "Pages, ex. 1,3,5",
    pageRangesInput: "Plages, ex. 1-3,5-7",
    outputBasename: "Nom de base",
    split: "Diviser",
    splitting: "Division...",
    resultTitle: "Résultat prêt",
    downloadArchive: "Télécharger le ZIP",
    downloadFile: "Télécharger",
    signInTitle: "Connexion requise",
    signInDescription: "Les outils PDF utilisent votre quota authentifié.",
    signIn: "Se connecter",
    loading: "Vérification du compte...",
    noFile: "Choisissez un PDF.",
    invalidFile: "Seuls les PDF sont pris en charge.",
    tooLarge: "Le PDF doit faire 50 Mo ou moins.",
    badSelected: "Les pages sélectionnées sont requises.",
    badRanges: "Les plages sont requises.",
    failed: "Impossible de diviser le PDF.",
  },
};

export const generateQuestionsPageTranslations = {
  en: {
    badge: "Generate questions",
    title: "Generate exam-style questions and fully worked answers",
    description:
      "Upload a PDF or Word document, paste notes, or enter a topic. ReDOCX creates checked, exam-style questions first, then can generate matching answers with full workings for calculations.",
    fileMode: "Upload file",
    textMode: "Inline text",
    uploadTitle: "Upload content for question generation",
    allowedFileInputs:
      "Allowed: .pdf and .docx. Rejected automatically: .png, .jpg, .jpeg, and unsupported formats.",
    outputExtensionWillBe: "Output extension will be",
    pasteTextLabel: "Paste notes or enter a topic",
    pasteTextPlaceholder:
      "Enter a topic such as quadratic equations, mechanics, stoichiometry, or price elasticity; or paste your notes...",
    inlineTextTreatedAs:
      "Inline text is treated as .txt, so generated questions and answers can be shown inline.",
    unsupportedFileType:
      "Unsupported file type: {ext}. Only .pdf and .docx uploads are allowed. PNG, JPG, JPEG and other image formats are rejected.",
    fileTooLarge: "File is too large. Maximum allowed size is {maxSize} MB.",
    sourceRequired: "Please provide a supported document or inline text.",
    questionsPotentialIssue: "Something went wrong while generating questions.",
    answersPotentialIssue: "Something went wrong while generating answers.",
    generatingQuestions: "Generating questions...",
    generatingAnswers: "Generating answers...",
    generateQuestions: "Generate questions",
    generateAnswers: "Generate answers",
    skipAnswers: "No, keep questions only",
    resetFlow: "Start over",
    formatPolicy: "Format policy",
    policySubtitle: "Two-step generation with exam-quality checks",
    allowedUploadsLabel: "Allowed uploads:",
    inlineInputLabel: "Inline input:",
    rejectedAutomaticallyLabel: "Rejected automatically:",
    outputRuleLabel: "Output rule:",
    inlineInputValue: "treated as .txt",
    rejectedAutomaticallyValue: ".png, .jpg, .jpeg, and unsupported file types",
    outputRuleValue:
      "questions are checked for exam suitability and solvability; answers include workings after user confirmation",
    questionsOutputTitle: "Generated questions",
    answersOutputTitle: "Generated answers",
    previewEmpty:
      "Your generated questions will appear here. After that, you can decide whether ReDOCX should generate answers.",
    answersPreviewEmpty:
      "Detailed answers and calculation workings will appear here only if you choose to generate them.",
    outputExtensionLabel: "Output extension:",
    inputFile: "Input file(s)",
    inputText: "Inline text",
    detectedSource: "Source",
    questionCount: "Question count",
    answerCount: "Answer count",
    downloadQuestionsFile: "Download generated questions",
    downloadAnswersFile: "Download generated answers",
    answerPromptTitle: "Do you want me to generate answers to these questions?",
    answerPromptDescription:
      "Answer generation uses the original source content and the exact numbered questions, then checks calculations and shows the full method.",
    declinedTitle: "Questions kept without answers",
    declinedDescription:
      "No answer-generation request was sent. You can still generate answers from these questions while this page remains open.",
    cannotGenerateAnswersTitle: "Question text is unavailable",
    cannotGenerateAnswersDescription:
      "The numbered question text could not be verified. Regenerate the questions before requesting answers.",
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
    title:
      "Générez des questions de type examen et des solutions entièrement détaillées",
    description:
      "Téléversez un PDF ou un document Word, collez des notes ou saisissez un sujet. ReDOCX crée d’abord des questions de type examen vérifiées, puis peut générer les réponses correspondantes avec tous les calculs.",
    fileMode: "Téléverser un fichier",
    textMode: "Texte inline",
    uploadTitle: "Téléverser le contenu pour générer des questions",
    allowedFileInputs:
      "Autorisés : .pdf et .docx. Rejetés automatiquement : .png, .jpg, .jpeg et les formats non pris en charge.",
    outputExtensionWillBe: "L’extension de sortie sera",
    pasteTextLabel: "Coller des notes ou saisir un sujet",
    pasteTextPlaceholder:
      "Saisissez un sujet comme les équations quadratiques, la mécanique, la stœchiométrie ou l’élasticité-prix ; ou collez vos notes...",
    inlineTextTreatedAs:
      "Le texte inline est traité comme .txt, donc les questions et réponses peuvent être affichées inline.",
    unsupportedFileType:
      "Type de fichier non pris en charge : {ext}. Seuls les fichiers .pdf et .docx sont autorisés. PNG, JPG, JPEG et les autres formats image sont rejetés.",
    fileTooLarge:
      "Le fichier est trop volumineux. La taille maximale autorisée est de {maxSize} Mo.",
    sourceRequired:
      "Veuillez fournir un document pris en charge ou du texte inline.",
    questionsPotentialIssue:
      "Une erreur s’est produite lors de la génération des questions.",
    answersPotentialIssue:
      "Une erreur s’est produite lors de la génération des réponses.",
    generatingQuestions: "Génération des questions...",
    generatingAnswers: "Génération des réponses...",
    generateQuestions: "Générer les questions",
    generateAnswers: "Générer les réponses",
    skipAnswers: "Non, garder seulement les questions",
    resetFlow: "Recommencer",
    formatPolicy: "Règles de format",
    policySubtitle:
      "Génération en deux étapes avec contrôles de qualité d’examen",
    allowedUploadsLabel: "Téléversements autorisés :",
    inlineInputLabel: "Entrée inline :",
    rejectedAutomaticallyLabel: "Rejetés automatiquement :",
    outputRuleLabel: "Règle de sortie :",
    inlineInputValue: "traité comme .txt",
    rejectedAutomaticallyValue:
      ".png, .jpg, .jpeg et formats non pris en charge",
    outputRuleValue:
      "les questions sont vérifiées pour leur pertinence et leur résolution ; les réponses détaillent les calculs après confirmation",
    questionsOutputTitle: "Questions générées",
    answersOutputTitle: "Réponses générées",
    previewEmpty:
      "Vos questions générées apparaîtront ici. Ensuite, vous pourrez décider si ReDOCX doit générer les réponses.",
    answersPreviewEmpty:
      "Les réponses détaillées et les calculs apparaîtront ici uniquement si vous choisissez de les générer.",
    outputExtensionLabel: "Extension de sortie :",
    inputFile: "Fichier(s) d’entrée",
    inputText: "Texte inline",
    detectedSource: "Source",
    questionCount: "Nombre de questions",
    answerCount: "Nombre de réponses",
    downloadQuestionsFile: "Télécharger les questions générées",
    downloadAnswersFile: "Télécharger les réponses générées",
    answerPromptTitle:
      "Voulez-vous que je génère les réponses à ces questions ?",
    answerPromptDescription:
      "La génération utilise le contenu source et les questions numérotées exactes, puis vérifie les calculs et présente toute la méthode.",
    declinedTitle: "Questions conservées sans réponses",
    declinedDescription:
      "Aucune requête de génération de réponses n’a été envoyée. Vous pouvez encore générer les réponses à partir de ces questions tant que cette page reste ouverte.",
    cannotGenerateAnswersTitle: "Le texte des questions est indisponible",
    cannotGenerateAnswersDescription:
      "Le texte numéroté des questions n’a pas pu être vérifié. Régénérez les questions avant de demander les réponses.",
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
    loadPotentialIssue: "Could not load billing options.",
    upgradePotentialIssue: "Could not start the upgrade.",
    checkoutNotConfigured:
      "This upgrade is allowed, but checkout is not configured yet.",
    organizationName: "Organization name",
    organizationNameHelp:
      "Choose the name your members will see in your Business or Enterprise workspace. You can change it later in Team Settings.",
    organizationNamePlaceholder: "For example, ReDOCX Software",
    organizationNameRequired:
      "Enter an organization name before choosing a Business or Enterprise plan.",
    organizationNameTooShort:
      "Organization name must contain at least 2 characters.",
    organizationNameTooLong: "Organization name cannot exceed 100 characters.",
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
    loadPotentialIssue: "Impossible de charger les options de facturation.",
    upgradePotentialIssue: "Impossible de démarrer la mise à niveau.",
    checkoutNotConfigured:
      "Cette mise à niveau est autorisée, mais le paiement n’est pas encore configuré.",
    organizationName: "Nom de l’organisation",
    organizationNameHelp:
      "Choisissez le nom visible par les membres de votre espace Business ou Enterprise. Vous pourrez le modifier plus tard dans les paramètres de l’équipe.",
    organizationNamePlaceholder: "Par exemple, ReDOCX Software",
    organizationNameRequired:
      "Saisissez un nom d’organisation avant de choisir un forfait Business ou Enterprise.",
    organizationNameTooShort:
      "Le nom de l’organisation doit comporter au moins 2 caractères.",
    organizationNameTooLong:
      "Le nom de l’organisation ne peut pas dépasser 100 caractères.",
    signInTitle: "Connexion requise",
    signInDescription:
      "Les options de facturation et de mise à niveau sont réservées aux utilisateurs authentifiés.",
    signIn: "Se connecter",
  },
};


// Centralized supplemental page copy migrated from page modules.

export const processedOutputActionTranslations = {
  en: {
    print: "Print",
    share: "Share",
    shareToApps: "Share to other apps",
    shareToMembers: "Share with ReDOCX members",
    preparingShare: "Preparing file for secure sharing...",
    nativeShareUnsupported:
      "This browser cannot share this file directly to other apps. Download the file and share it from your device instead.",
    printPopupBlocked:
      "The print window was blocked by your browser. Allow pop-ups for ReDOCX and try again.",
    printPreparing: "Preparing a secure print preview...",
    printFailed: "Could not prepare this output for printing.",
    printUnsupported:
      "This output format cannot be printed directly. Download the file and open it in an application that supports printing.",
    printArchiveUnsupported:
      "ZIP packages cannot be printed directly. Download and extract the package, then print the required document.",
    memberShareTitle: "Share securely with organization members",
    organization: "Organization",
    recipients: "Recipients",
    noOrganizations:
      "No active Business or Enterprise organization is available for member sharing.",
    noMembers: "No other active members are available in this organization.",
    selectRecipients: "Choose at least one organization member.",
    sharing: "Sharing securely...",
    shareSelected: "Share with selected members",
    cancel: "Cancel",
    close: "Close",
    memberLimit: "Choose up to 50 members.",
    teamFileTooLarge:
      "This file exceeds the 20 MB secure team-attachment limit and cannot be shared to ReDOCX members.",
    teamFileTypeUnsupported:
      "This file type is not permitted by ReDOCX secure team attachments. Download it or share it through another app instead.",
    shareSuccess: "Shared securely with ReDOCX organization members.",
    sharePartial:
      "The file was shared with some members, but one or more deliveries failed.",
    signInRequired: "Sign in to share with ReDOCX organization members.",
    outputActions: "Output actions",
    fileShared: "File shared successfully.",
    organizationMember: "Organization member",
    outputTitle: "ReDOCX output",
    securePrintPreview: "ReDOCX secure print preview",
    sharedFrom: "Shared from ReDOCX: {filename}",
    shareOneOfMany: "The file was shared with 1 of {total} selected members. {error}",
    shareManyOfMany: "The file was shared with {delivered} of {total} selected members.",
  },
  fr: {
    print: "Imprimer",
    share: "Partager",
    shareToApps: "Partager vers d’autres applications",
    shareToMembers: "Partager avec des membres ReDOCX",
    preparingShare: "Préparation du fichier pour un partage sécurisé...",
    nativeShareUnsupported:
      "Ce navigateur ne peut pas partager directement ce fichier vers d’autres applications. Téléchargez le fichier puis partagez-le depuis votre appareil.",
    printPopupBlocked:
      "La fenêtre d’impression a été bloquée. Autorisez les fenêtres contextuelles pour ReDOCX puis réessayez.",
    printPreparing: "Préparation d’un aperçu d’impression sécurisé...",
    printFailed: "Impossible de préparer cette sortie pour l’impression.",
    printUnsupported:
      "Ce format de sortie ne peut pas être imprimé directement. Téléchargez le fichier et ouvrez-le dans une application compatible avec l’impression.",
    printArchiveUnsupported:
      "Les archives ZIP ne peuvent pas être imprimées directement. Téléchargez et extrayez l’archive, puis imprimez le document requis.",
    memberShareTitle: "Partager de manière sécurisée avec les membres de l’organisation",
    organization: "Organisation",
    recipients: "Destinataires",
    noOrganizations:
      "Aucune organisation Business ou Enterprise active n’est disponible pour le partage entre membres.",
    noMembers: "Aucun autre membre actif n’est disponible dans cette organisation.",
    selectRecipients: "Choisissez au moins un membre de l’organisation.",
    sharing: "Partage sécurisé en cours...",
    shareSelected: "Partager avec les membres sélectionnés",
    cancel: "Annuler",
    close: "Fermer",
    memberLimit: "Choisissez jusqu’à 50 membres.",
    teamFileTooLarge:
      "Ce fichier dépasse la limite de 20 Mo des pièces jointes d’équipe sécurisées et ne peut pas être partagé avec des membres ReDOCX.",
    teamFileTypeUnsupported:
      "Ce type de fichier n’est pas autorisé par les pièces jointes d’équipe sécurisées ReDOCX. Téléchargez-le ou partagez-le via une autre application.",
    shareSuccess: "Partage sécurisé effectué avec les membres de l’organisation ReDOCX.",
    sharePartial:
      "Le fichier a été partagé avec certains membres, mais une ou plusieurs livraisons ont échoué.",
    signInRequired: "Connectez-vous pour partager avec des membres de votre organisation ReDOCX.",
    outputActions: "Actions de sortie",
    fileShared: "Fichier partagé avec succès.",
    organizationMember: "Membre de l’organisation",
    outputTitle: "Sortie ReDOCX",
    securePrintPreview: "Aperçu d’impression sécurisé ReDOCX",
    sharedFrom: "Partagé depuis ReDOCX : {filename}",
    shareOneOfMany: "Le fichier a été partagé avec 1 membre sur {total}. {error}",
    shareManyOfMany: "Le fichier a été partagé avec {delivered} membres sur {total}.",
  },
};

export const billingSeatPricingTranslations = {
  en: {
    perSeat: "per seat",
    seatsLabel: "Seats (including you)",
    businessHelp: "Choose between 1 and 19 seats. You count as one seat.",
    enterpriseHelp: "Choose the number of seats you need. You count as one seat.",
    invalidSeats: "Enter a whole number of seats greater than or equal to 1.",
    businessLimit: "Business supports a maximum of 19 seats.",
    total: "Recurring total",
  },
  fr: {
    perSeat: "par siège",
    seatsLabel: "Sièges (vous compris)",
    businessHelp: "Choisissez entre 1 et 19 sièges. Vous comptez comme un siège.",
    enterpriseHelp: "Choisissez le nombre de sièges nécessaires. Vous comptez comme un siège.",
    invalidSeats: "Saisissez un nombre entier de sièges supérieur ou égal à 1.",
    businessLimit: "Business prend en charge un maximum de 19 sièges.",
    total: "Total récurrent",
  },
};

export const billingSubscriptionManagementTranslations = {
  en: {
    title: "Manage subscription",
    description:
      "Your paid plan is fixed until the current paid period ends. Cancelling stops the next renewal but keeps access through that date; resuming turns renewal back on for the same plan.",
    periodLocked: "Plan changes are locked until the current paid period ends.",
    planChangesAvailable: "Plan changes available after",
    cancel: "Cancel renewal",
    cancelling: "Scheduling cancellation…",
    resume: "Resume renewal",
    resuming: "Resuming…",
    downgrade: "Schedule downgrade",
    downgrading: "Scheduling downgrade…",
    confirmCancel:
      "Cancel automatic renewal? Your paid access will remain available until the current paid period ends.",
    confirmDowngrade:
      "Schedule this downgrade for the end of the current paid period?",
    periodEnds: "Current paid period ends",
    pendingPlan: "Scheduled plan",
    graceTitle: "Payment needs attention",
    graceDescription:
      "A renewal payment failed. Paid access remains available during the grace period. Update the payment method with your billing provider before",
    failureCount: "Failed payment events",
    suspendedTitle: "Paid access suspended",
    suspendedDescription:
      "A refund, dispute, or chargeback has suspended paid access. Cancellation remains available to stop future billing. Contact support after the provider case is resolved.",
  },
  fr: {
    title: "Gérer l’abonnement",
    description:
      "Votre forfait payant reste inchangé jusqu’à la fin de la période payée en cours. L’annulation arrête le prochain renouvellement tout en conservant l’accès jusque-là ; la reprise réactive le renouvellement du même forfait.",
    periodLocked: "Les changements de forfait sont bloqués jusqu’à la fin de la période payée en cours.",
    planChangesAvailable: "Changements de forfait disponibles après le",
    cancel: "Annuler le renouvellement",
    cancelling: "Planification de l’annulation…",
    resume: "Reprendre le renouvellement",
    resuming: "Reprise…",
    downgrade: "Planifier la rétrogradation",
    downgrading: "Planification…",
    confirmCancel:
      "Annuler le renouvellement automatique ? Votre accès payant restera disponible jusqu’à la fin de la période payée.",
    confirmDowngrade:
      "Planifier cette rétrogradation pour la fin de la période payée ?",
    periodEnds: "Fin de la période payée",
    pendingPlan: "Offre planifiée",
    graceTitle: "Paiement à vérifier",
    graceDescription:
      "Un paiement de renouvellement a échoué. L’accès payant reste disponible pendant le délai de grâce. Mettez à jour le moyen de paiement auprès de votre fournisseur avant le",
    failureCount: "Échecs de paiement",
    suspendedTitle: "Accès payant suspendu",
    suspendedDescription:
      "Un remboursement, un litige ou une rétrofacturation a suspendu l’accès payant. L’annulation reste disponible pour arrêter les futurs prélèvements. Contactez le support après la résolution du dossier fournisseur.",
  },
};

export const billingProviderFallbackTranslations = {
  en: {
    title: "Choose payment provider",
    description:
      "Paystack is recommended for Nigerian and African users. Stripe is recommended for US and European users. You can choose either provider before upgrading.",
    recommended: "Recommended",
    selected: "Selected",
    configured: "Ready",
    notConfigured: "Not configured",
    checkoutWith: "Checkout with {provider}",
    unavailableForPlan:
      "This payment provider is not configured for this plan yet.",
    paystack: {
      name: "Paystack",
      summary:
        "Nigeria / Africa cards, bank transfer, USSD, and local payment rails.",
      region_label: "Nigeria / Africa",
    },
    stripe: {
      name: "Stripe",
      summary: "US / Europe cards and international card checkout.",
      region_label: "US / Europe",
    },
  },
  fr: {
    title: "Choisir le fournisseur de paiement",
    description:
      "Paystack est recommandé pour les utilisateurs nigérians et africains. Stripe est recommandé pour les États-Unis et l’Europe. Vous pouvez choisir le fournisseur avant la mise à niveau.",
    recommended: "Recommandé",
    selected: "Sélectionné",
    configured: "Prêt",
    notConfigured: "Non configuré",
    checkoutWith: "Paiement avec {provider}",
    unavailableForPlan:
      "Ce fournisseur de paiement n’est pas encore configuré pour ce forfait.",
    paystack: {
      name: "Paystack",
      summary:
        "Cartes Nigeria / Afrique, virement bancaire, USSD et moyens locaux.",
      region_label: "Nigeria / Afrique",
    },
    stripe: {
      name: "Stripe",
      summary: "Cartes États-Unis / Europe et paiement international.",
      region_label: "États-Unis / Europe",
    },
  },
};

export const billingFallbackPlanTranslations = {
  en: {
    apiMissing:
      "Billing API route is not connected yet. Showing a local plan preview for now.",
    checkoutComingSoon: "Checkout is not connected yet.",
    currentPlanReason: "This is your current plan.",
    checkoutFinalizing:
      "Payment received. ReDOCX is securely confirming it with Paystack.",
    checkoutConfirmationDelayed:
      "Payment was received, but activation has not completed yet. Do not make another payment. ReDOCX will keep reconciling the existing Paystack transaction. Refresh shortly or contact support with your payment reference if this persists.",
    checkoutCancelled:
      "Checkout was cancelled. No changes were made to your plan.",
    plans: {
      free: {
        name: "Free",
        summary: "Start using core ReDOCX tools with limited monthly usage.",
        price_label: "$0",
        billing_period: "Monthly",
        account_count_label: "1 account",
        features: [
          "Limited document processing",
          "Core AI document tools",
          "Basic PDF features",
        ],
      },
      personal: {
        name: "Personal",
        summary: "Higher limits for individual document workflows.",
        price_label: "Pricing unavailable",
        billing_period: "Monthly",
        account_count_label: "1 account",
        features: [
          "More document processing",
          "Redaction and masking workflows",
          "Priority personal usage",
        ],
      },
      business: {
        name: "Business",
        summary: "Team plan for shared document work and collaboration.",
        price_label: "Pricing unavailable",
        billing_period: "Monthly",
        account_count_label: "1–19 seats",
        features: [
          "Team access",
          "Organization collaboration",
          "Business document workflows",
        ],
      },
      enterprise: {
        name: "Enterprise",
        summary:
          "Custom usage, support, and deployment options for larger teams.",
        price_label: "Pricing unavailable",
        billing_period: "Monthly",
        account_count_label: "1+ seats",
        features: ["Custom limits", "Advanced support", "Enterprise controls"],
      },
    },
  },
  fr: {
    apiMissing:
      "La route API de facturation n’est pas encore connectée. Affichage temporaire d’un aperçu local des forfaits.",
    checkoutComingSoon: "Le paiement n’est pas encore connecté.",
    currentPlanReason: "Ceci est votre forfait actuel.",
    checkoutFinalizing:
      "Paiement reçu. ReDOCX le confirme de manière sécurisée auprès de Paystack.",
    checkoutConfirmationDelayed:
      "Le paiement a été reçu, mais l’activation n’est pas encore terminée. N’effectuez pas un nouveau paiement. ReDOCX continuera à rapprocher la transaction Paystack existante. Actualisez la page sous peu ou contactez le support avec votre référence de paiement si le problème persiste.",
    checkoutCancelled:
      "Le paiement a été annulé. Aucun changement n’a été apporté à votre forfait.",
    plans: {
      free: {
        name: "Gratuit",
        summary:
          "Commencez avec les outils ReDOCX essentiels et une utilisation mensuelle limitée.",
        price_label: "0 $",
        billing_period: "Mensuel",
        account_count_label: "1 compte",
        features: [
          "Traitement de documents limité",
          "Outils IA essentiels",
          "Fonctions PDF de base",
        ],
      },
      personal: {
        name: "Personnel",
        summary:
          "Des limites plus élevées pour les flux de documents individuels.",
        price_label: "Tarification indisponible",
        billing_period: "Mensuel",
        account_count_label: "1 compte",
        features: [
          "Plus de traitement de documents",
          "Flux de masquage et de rédaction",
          "Utilisation personnelle prioritaire",
        ],
      },
      business: {
        name: "Business",
        summary: "Forfait d’équipe pour le travail documentaire partagé.",
        price_label: "Tarification indisponible",
        billing_period: "Mensuel",
        account_count_label: "1–19 sièges",
        features: [
          "Accès d’équipe",
          "Collaboration d’organisation",
          "Flux documentaires business",
        ],
      },
      enterprise: {
        name: "Enterprise",
        summary:
          "Options personnalisées d’utilisation, de support et de déploiement.",
        price_label: "Tarification indisponible",
        billing_period: "Mensuel",
        account_count_label: "1+ sièges",
        features: [
          "Limites personnalisées",
          "Support avancé",
          "Contrôles enterprise",
        ],
      },
    },
  },
};

export const editPdfVisualTranslations = {
  en: {
    editorTitle: "Edit directly on the document",
    editorHelp:
      "Choose a tool, then click and drag over the PDF. ReDOCX converts your visual changes into secure PDF operations when you submit.",
    loadingPdf: "Preparing your PDF…",
    renderFailed:
      "The PDF could not be displayed. Confirm that it is a valid, unencrypted PDF.",
    select: "Select",
    correctText: "Correct text",
    addText: "Add text",
    shape: "Shape",
    comment: "Comment",
    removeText: "Remove text",
    highlight: "Highlight",
    whiteout: "Whiteout",
    draw: "Draw",
    image: "Image",
    removeImage: "Remove image",
    signature: "Signature",
    removeSignature: "Remove signature",
    addAndMarkTools: "Add and mark",
    removeTools: "Remove content",
    dragInstruction: "Drag on the page to place this edit.",
    drawInstruction: "Draw directly on the page.",
    imageInstruction: "Choose an image, then drag on the page to place it.",
    selectInstruction: "Select an edit to move, resize, update, or delete it.",
    correctTextInstruction:
      "Click detected text to edit it immediately, or drag around several words or lines.",
    removeInstruction:
      "Drag over the exact content to remove. The operation permanently changes that region in the output PDF.",
    keyboardHelp:
      "Keyboard: arrows move, Shift + arrows resize, Delete removes, and Ctrl/Cmd + Z restores edits. Double-click text to type on the page.",
    currentPage: "Page",
    goToPage: "Go to page",
    previousPage: "Previous page",
    nextPage: "Next page",
    zoomOut: "Zoom out",
    zoomIn: "Zoom in",
    undo: "Undo",
    redo: "Redo",
    clearAll: "Clear all edits",
    inspector: "Edit properties",
    noSelection: "Select an edit on the document to change its properties.",
    replacementText: "Replacement text",
    insertedText: "Text",
    fontFamily: "Font",
    fontSize: "Font size",
    color: "Color",
    bold: "Bold",
    italic: "Italic",
    underline: "Underline",
    strikethrough: "Strikethrough",
    alignment: "Alignment",
    alignLeft: "Align left",
    alignCenter: "Align center",
    alignRight: "Align right",
    justify: "Justify",
    lineSpacing: "Line spacing",
    textOpacity: "Text opacity",
    background: "Text box background",
    border: "Border",
    borderWidth: "Border width",
    padding: "Inner spacing",
    rotation: "Rotation",
    link: "Link",
    linkHelp: "Optional http, https, or mailto link for this text box.",
    autoFit: "Shrink text to fit when needed",
    minimumFontSize: "Minimum font size",
    opacity: "Opacity",
    strokeWidth: "Stroke width",
    shapeType: "Shape type",
    rectangleShape: "Rectangle",
    ellipseShape: "Ellipse",
    lineShape: "Line",
    arrowShape: "Arrow",
    fill: "Fill",
    noFill: "No fill",
    commentText: "Comment",
    commentAuthor: "Author",
    imageFit: "Image fit",
    contain: "Keep proportions",
    stretch: "Fill frame",
    replaceImage: "Replace image",
    signatureType: "Signature type",
    typed: "Typed",
    drawn: "Drawn",
    uploaded: "Uploaded image",
    typedName: "Name shown as signature",
    signaturePad: "Draw signature",
    clearSignature: "Clear signature",
    signatureConsent:
      "I confirm that I am authorized to apply this signature to the document.",
    deleteEdit: "Delete edit",
    duplicateEdit: "Duplicate edit",
    bringForward: "Bring forward",
    sendBackward: "Send backward",
    edits: "Document edits",
    noEdits: "No edits have been added yet.",
    correction: "Text correction",
    textAddition: "Added text",
    textRemoval: "Removed text",
    imageAddition: "Added image",
    imageRemoval: "Removed image",
    signatureAddition: "Signature",
    signatureRemoval: "Removed signature",
    drawing: "Drawing",
    shapeAddition: "Shape",
    commentAddition: "Comment",
    editCount: "edits",
    chooseImage: "Choose image",
    chooseSignatureImage: "Choose signature image",
    noText: "Every text correction or text addition must contain text.",
    noDrawing: "Every drawing must contain at least one stroke.",
    noImage: "Every image edit must include an image.",
    noSignature:
      "Every signature must include a name, drawing, or uploaded image.",
    noComment: "Every comment must contain text.",
    noConsent: "Signature authorization must be confirmed before processing.",
    badLink: "Links must begin with http://, https://, or mailto:.",
    tooManyOperations:
      "This document exceeds the 2000-operation processing limit.",
    tooManyAssets:
      "A single edit request can include at most 25 image or signature files.",
    badPlacement: "Create a larger edit region inside the page.",
    removeFileConfirm: "Remove this PDF and discard all edits?",
    clearConfirm: "Discard all edits on this PDF?",
    visualWorkflow: "Visual editing workspace",
    hiddenCoordinates:
      "Placement is captured automatically. Users never need to enter page coordinates.",
    selectedFile: "Selected PDF",
    changePdf: "Change PDF",
    readyToProcess:
      "Review the visual edits, then submit the document for processing.",
    processingNote:
      "Text correction removes text in the selected region before inserting the replacement. Whiteout removes all selected content and covers the region in white.",
    digitalSignatureWarning:
      "Editing changes the PDF file and can invalidate existing certificate-based digital signatures. Edit an unsigned copy or plan to sign the finished PDF again.",
    verifiedOutput: "Verified output",
    preparingRequest: "Preparing and validating your edits…",
    secureProcessing: "Uploading and processing the PDF securely…",
    cancelProcessing: "Cancel",
    processingCancelled: "PDF editing was cancelled.",
    unsavedWarning:
      "Your visual edits are not saved until you process the PDF.",
    unsavedLeaveConfirm: "Leave this editor and discard all unprocessed edits?",
    incompleteResult:
      "The PDF editor did not apply every requested edit. No result was accepted.",
    resize: "Resize edit",
    inlineEdit: "Double-click to edit text directly",
    detectedText: "Detected PDF text",
  },
  fr: {
    editorTitle: "Modifiez directement le document",
    editorHelp:
      "Choisissez un outil, puis cliquez-glissez sur le PDF. ReDOCX convertit vos modifications visuelles en opérations PDF sécurisées lors de l’envoi.",
    loadingPdf: "Préparation du PDF…",
    renderFailed:
      "Le PDF ne peut pas être affiché. Vérifiez qu’il est valide et non chiffré.",
    select: "Sélectionner",
    correctText: "Corriger le texte",
    addText: "Ajouter du texte",
    shape: "Forme",
    comment: "Commentaire",
    removeText: "Supprimer le texte",
    highlight: "Surligner",
    whiteout: "Effacer",
    draw: "Dessiner",
    image: "Image",
    removeImage: "Supprimer l’image",
    signature: "Signature",
    removeSignature: "Supprimer la signature",
    addAndMarkTools: "Ajouter et annoter",
    removeTools: "Supprimer du contenu",
    dragInstruction:
      "Faites glisser sur la page pour placer cette modification.",
    drawInstruction: "Dessinez directement sur la page.",
    imageInstruction:
      "Choisissez une image, puis faites glisser pour la placer.",
    selectInstruction:
      "Sélectionnez une modification pour la déplacer, la redimensionner ou la supprimer.",
    correctTextInstruction:
      "Cliquez sur le texte détecté pour le modifier immédiatement, ou encadrez plusieurs mots ou lignes.",
    removeInstruction:
      "Faites glisser précisément sur le contenu à supprimer. L’opération modifie définitivement cette zone dans le PDF de sortie.",
    keyboardHelp:
      "Clavier : les flèches déplacent, Maj + flèches redimensionne, Suppr retire et Ctrl/Cmd + Z restaure. Double-cliquez sur le texte pour saisir directement.",
    currentPage: "Page",
    goToPage: "Aller à la page",
    previousPage: "Page précédente",
    nextPage: "Page suivante",
    zoomOut: "Réduire",
    zoomIn: "Agrandir",
    undo: "Annuler",
    redo: "Rétablir",
    clearAll: "Effacer toutes les modifications",
    inspector: "Propriétés",
    noSelection:
      "Sélectionnez une modification sur le document pour la mettre à jour.",
    replacementText: "Texte de remplacement",
    insertedText: "Texte",
    fontFamily: "Police",
    fontSize: "Taille de police",
    color: "Couleur",
    bold: "Gras",
    italic: "Italique",
    underline: "Souligné",
    strikethrough: "Barré",
    alignment: "Alignement",
    alignLeft: "Aligner à gauche",
    alignCenter: "Centrer",
    alignRight: "Aligner à droite",
    justify: "Justifier",
    lineSpacing: "Interligne",
    textOpacity: "Opacité du texte",
    background: "Arrière-plan de la zone",
    border: "Bordure",
    borderWidth: "Épaisseur de bordure",
    padding: "Marge intérieure",
    rotation: "Rotation",
    link: "Lien",
    linkHelp: "Lien facultatif http, https ou mailto pour cette zone de texte.",
    autoFit: "Réduire le texte pour l’adapter si nécessaire",
    minimumFontSize: "Taille minimale",
    opacity: "Opacité",
    strokeWidth: "Épaisseur du trait",
    shapeType: "Type de forme",
    rectangleShape: "Rectangle",
    ellipseShape: "Ellipse",
    lineShape: "Ligne",
    arrowShape: "Flèche",
    fill: "Remplissage",
    noFill: "Sans remplissage",
    commentText: "Commentaire",
    commentAuthor: "Auteur",
    imageFit: "Ajustement de l’image",
    contain: "Conserver les proportions",
    stretch: "Remplir le cadre",
    replaceImage: "Remplacer l’image",
    signatureType: "Type de signature",
    typed: "Saisie",
    drawn: "Dessinée",
    uploaded: "Image importée",
    typedName: "Nom affiché comme signature",
    signaturePad: "Dessiner la signature",
    clearSignature: "Effacer la signature",
    signatureConsent:
      "Je confirme être autorisé à apposer cette signature au document.",
    deleteEdit: "Supprimer la modification",
    duplicateEdit: "Dupliquer la modification",
    bringForward: "Avancer",
    sendBackward: "Reculer",
    edits: "Modifications du document",
    noEdits: "Aucune modification n’a encore été ajoutée.",
    correction: "Correction de texte",
    textAddition: "Texte ajouté",
    textRemoval: "Texte supprimé",
    imageAddition: "Image ajoutée",
    imageRemoval: "Image supprimée",
    signatureAddition: "Signature",
    signatureRemoval: "Signature supprimée",
    drawing: "Dessin",
    shapeAddition: "Forme",
    commentAddition: "Commentaire",
    editCount: "modifications",
    chooseImage: "Choisir une image",
    chooseSignatureImage: "Choisir une image de signature",
    noText: "Chaque correction ou ajout de texte doit contenir du texte.",
    noDrawing: "Chaque dessin doit contenir au moins un trait.",
    noImage: "Chaque modification d’image doit inclure une image.",
    noSignature:
      "Chaque signature doit inclure un nom, un dessin ou une image.",
    noComment: "Chaque commentaire doit contenir du texte.",
    noConsent:
      "L’autorisation de signature doit être confirmée avant le traitement.",
    badLink: "Les liens doivent commencer par http://, https:// ou mailto:.",
    tooManyOperations: "Ce document dépasse la limite de 2000 opérations.",
    tooManyAssets:
      "Une demande peut contenir au maximum 25 images ou signatures.",
    badPlacement: "Créez une zone de modification plus grande dans la page.",
    removeFileConfirm: "Retirer ce PDF et supprimer toutes les modifications ?",
    clearConfirm: "Supprimer toutes les modifications de ce PDF ?",
    visualWorkflow: "Espace de modification visuelle",
    hiddenCoordinates:
      "Le placement est enregistré automatiquement. Aucune coordonnée de page n’est demandée.",
    selectedFile: "PDF sélectionné",
    changePdf: "Changer de PDF",
    readyToProcess:
      "Vérifiez les modifications visuelles, puis envoyez le document.",
    processingNote:
      "La correction supprime le texte dans la zone sélectionnée avant d’insérer le remplacement. L’effacement blanc supprime tout le contenu sélectionné et couvre la zone en blanc.",
    digitalSignatureWarning:
      "La modification change le fichier PDF et peut invalider les signatures numériques existantes fondées sur un certificat. Modifiez une copie non signée ou prévoyez de signer à nouveau le PDF final.",
    verifiedOutput: "Sortie vérifiée",
    preparingRequest: "Préparation et validation de vos modifications…",
    secureProcessing: "Envoi et traitement sécurisé du PDF…",
    cancelProcessing: "Annuler",
    processingCancelled: "La modification du PDF a été annulée.",
    unsavedWarning:
      "Vos modifications visuelles ne sont enregistrées qu’après le traitement du PDF.",
    unsavedLeaveConfirm:
      "Quitter cet éditeur et supprimer toutes les modifications non traitées ?",
    incompleteResult:
      "L’éditeur PDF n’a pas appliqué toutes les modifications demandées. Aucun résultat n’a été accepté.",
    resize: "Redimensionner la modification",
    inlineEdit: "Double-cliquez pour modifier le texte directement",
    detectedText: "Texte PDF détecté",
  },
};

export const dataProtectionDocumentTypeOptions = [
  { value: "invoice", labels: { en: "Invoice", fr: "Facture" } },
  { value: "receipt", labels: { en: "Receipt", fr: "Reçu" } },
  { value: "kyc_document", labels: { en: "KYC document", fr: "Document KYC" } },
  {
    value: "bank_statement",
    labels: { en: "Bank statement", fr: "Relevé bancaire" },
  },
  {
    value: "financial_statement",
    labels: { en: "Financial statement", fr: "États financiers" },
  },
  {
    value: "tax_document",
    labels: { en: "Tax document", fr: "Document fiscal" },
  },
  {
    value: "insurance_document",
    labels: { en: "Insurance document", fr: "Document d’assurance" },
  },
  {
    value: "contract",
    labels: { en: "Contract / agreement", fr: "Contrat / accord" },
  },
  {
    value: "legal_document",
    labels: { en: "Legal document", fr: "Document juridique" },
  },
  {
    value: "id_document",
    labels: { en: "Identity document", fr: "Pièce d’identité" },
  },
  {
    value: "medical_record",
    labels: { en: "Medical / health record", fr: "Dossier médical / santé" },
  },
  {
    value: "academic_record",
    labels: {
      en: "Academic record / transcript",
      fr: "Dossier académique / relevé",
    },
  },
  {
    value: "academic_certificate",
    labels: {
      en: "Academic certificate / diploma",
      fr: "Diplôme / certificat académique",
    },
  },
  {
    value: "admission_enrollment_document",
    labels: {
      en: "Admission / enrollment document",
      fr: "Document d’admission / inscription",
    },
  },
  {
    value: "employment_hr_document",
    labels: { en: "Employment / HR document", fr: "Document emploi / RH" },
  },
  {
    value: "payroll_document",
    labels: { en: "Payroll / payslip", fr: "Paie / bulletin de salaire" },
  },
  {
    value: "resume_cv",
    labels: { en: "Résumé / CV", fr: "CV / curriculum vitæ" },
  },
  {
    value: "government_public_record",
    labels: {
      en: "Government / public record",
      fr: "Document gouvernemental / public",
    },
  },
  {
    value: "immigration_travel_document",
    labels: {
      en: "Immigration / travel document",
      fr: "Document d’immigration / voyage",
    },
  },
  {
    value: "property_real_estate_document",
    labels: {
      en: "Property / real-estate document",
      fr: "Document immobilier / foncier",
    },
  },
  {
    value: "business_corporate_document",
    labels: {
      en: "Business / corporate document",
      fr: "Document d’entreprise / société",
    },
  },
  {
    value: "audit_document",
    labels: { en: "Audit document", fr: "Document d’audit" },
  },
  {
    value: "compliance_regulatory_document",
    labels: {
      en: "Compliance / regulatory document",
      fr: "Document de conformité / réglementaire",
    },
  },
  {
    value: "research_technical_document",
    labels: {
      en: "Research / technical document",
      fr: "Document de recherche / technique",
    },
  },
  {
    value: "historical_archival_document",
    labels: {
      en: "Historical / archival document",
      fr: "Document historique / d’archives",
    },
  },
  {
    value: "correspondence",
    labels: {
      en: "Correspondence / letter / memo",
      fr: "Correspondance / lettre / note",
    },
  },
  {
    value: "application_form",
    labels: {
      en: "Application / registration form",
      fr: "Formulaire / demande",
    },
  },
  {
    value: "utility_telecom_document",
    labels: {
      en: "Utility / telecom document",
      fr: "Services publics / télécoms",
    },
  },
  {
    value: "procurement_document",
    labels: {
      en: "Procurement / purchasing document",
      fr: "Approvisionnement / achats",
    },
  },
  {
    value: "policy_procedure_document",
    labels: {
      en: "Policy / procedure / manual",
      fr: "Politique / procédure / manuel",
    },
  },
  {
    value: "general_document",
    labels: { en: "General document", fr: "Document général" },
  },
];

export const teamSettingsSupplementalTranslations = {
  en: {
    backToWorkspace: "Back to Projects & Team",
    backToDashboard: "Back to dashboard",
    unavailableTitle: "Team settings are unavailable",
    unavailableDescription:
      "Team settings are available only to paid Business or Enterprise organization members.",
    ownershipTransferredWithBilling:
      "Ownership transferred. The previous payer will not renew. The new owner must authorize the next renewal before the current paid period ends.",
    handoffTitle: "Next renewal payer",
    handoffDescription:
      "Authorize your payment method now for the next billing period. You will not be charged for the period that is already paid.",
    handoffScheduledDescription:
      "Your next renewal is authorized and scheduled to begin when the current paid period ends.",
    handoffStarts: "Next renewal begins",
    handoffAuthorize: "Authorize next renewal",
    handoffAuthorizing: "Authorizing…",
    handoffReady: "Authorized",
    handoffScheduled: "Next renewal is authorized and scheduled under the new owner.",
    handoffAuthorizationCancelled:
      "Billing authorization was cancelled. The previous owner will still not renew; authorize before the paid period ends to continue automatically.",
  },
  fr: {
    backToWorkspace: "Retour à Projets & équipe",
    backToDashboard: "Retour au tableau de bord",
    unavailableTitle: "Paramètres de l’équipe indisponibles",
    unavailableDescription:
      "Les paramètres de l’équipe sont réservés aux membres payants d’une organisation Business ou Enterprise.",
    ownershipTransferredWithBilling:
      "Propriété transférée. L’ancien payeur ne renouvellera pas. Le nouveau propriétaire doit autoriser le prochain renouvellement avant la fin de la période payée en cours.",
    handoffTitle: "Payeur du prochain renouvellement",
    handoffDescription:
      "Autorisez maintenant votre moyen de paiement pour la prochaine période. Aucun montant ne sera prélevé pour la période déjà payée.",
    handoffScheduledDescription:
      "Votre prochain renouvellement est autorisé et programmé pour commencer à la fin de la période payée en cours.",
    handoffStarts: "Début du prochain renouvellement",
    handoffAuthorize: "Autoriser le prochain renouvellement",
    handoffAuthorizing: "Autorisation…",
    handoffReady: "Autorisé",
    handoffScheduled: "Le prochain renouvellement est autorisé et programmé au nom du nouveau propriétaire.",
    handoffAuthorizationCancelled:
      "L’autorisation de facturation a été annulée. L’ancien propriétaire ne renouvellera toujours pas ; autorisez le paiement avant la fin de la période payée pour continuer automatiquement.",
  },
};

export const projectsTeamWorkspaceTranslations = {
  en: {
    title: "Projects & Team",
    subtitle:
      "Collaborate with your organization through messages, shared files, group workspaces, and video calls.",
    backToDashboard: "Back to dashboard",
    businessChats: "Business Chats",
    back: "Back",
    settings: "Settings",
    enableNotifications: "Enable notifications",
    disableNotifications: "Disable notifications",
    notificationsEnabled: "Notifications enabled",
    notificationsDisabled: "Notifications disabled",
    enablingNotifications: "Enabling…",
    disablingNotifications: "Disabling…",
    sendDocument: "Send document",
    sendDocumentDescription: "Choose a plan member and up to 50 documents to share.",
    chooseRecipient: "Choose a recipient",
    chooseDocument: "Choose documents",
    cancelDocument: "Cancel",
    preparingDocument: "Opening chat...",
    inviteMembersTitle: "Invite members to send documents",
    inviteMembersDescription:
      "Add another member to this Business or Enterprise plan before sharing documents.",
    contactAdminDescription:
      "Ask an organization owner or admin to invite another member before sharing documents.",
    inviteMembers: "Invite members",
    businessGroupChat: "Business group chat",
    subgroups: "Subgroup chats",
    createSubgroup: "New subgroup",
    createSubgroupTitle: "Create a subgroup",
    subgroupDescription:
      "Select members once, then message, call, and share attachments with only this group.",
    subgroupName: "Subgroup name",
    subgroupNamePlaceholder: "For example: Product launch",
    subgroupMembers: "Choose members",
    subgroupMemberLimit: "Up to {count} members including you",
    subgroupMinimum: "Choose at least two other members.",
    subgroupLimitReached: "This plan's subgroup member limit has been reached.",
    createSubgroupChat: "Create & open chat",
    createSubgroupAudioCall: "Create & start audio call",
    createSubgroupVideoCall: "Create & start video call",
    creatingSubgroup: "Creating subgroup…",
    cancel: "Cancel",
    refresh: "Refresh",
    teamMembers: "Team members",
    message: "Message",
    call: "Call",
    audioCall: "Audio call",
    videoCall: "Video call",
    you: "You",
    recentlyJoined: "Recently joined",
    groupWorkspace: "Team group chat",
    createGroupChat: "Create group chat",
    openGroupChat: "Open group chat",
    callGroup: "Video call group",
    ownerOnlyGroup:
      "Only the organization owner can create the team group chat.",
    noMembers: "No other active members found yet.",
    messages: "Messages",
    chooseConversation:
      "Choose a member or the team group chat to start messaging.",
    messagePlaceholder: "Write a message...",
    send: "Send",
    sending: "Sending...",
    realtimeConnecting: "Connecting...",
    messagePending: "Sending...",
    messageFailed: "Failed to send",
    attachFile: "Attach file",
    removeAttachment: "Remove attachment",
    selectedAttachment: "Selected attachment",
    uploadingAttachment: "Uploading...",
    openAttachment: "Open attachment",
    attachmentTooLarge: "Attachment is too large. Maximum size is 20 MB.",
    attachmentUnsupported:
      "This file type is not allowed for secure team messaging.",
    attachmentSecured: "Malware-scanned and encrypted",
    attachmentUnavailable:
      "This legacy attachment is locked until its security migration is complete.",
    attachmentFailed: "Could not send attachments.",
    attachmentTooMany: "A message may contain at most 50 attachments.",
    attachmentMessageTooLarge: "The combined attachment size exceeds 1000 MB.",
    selectedAttachments: "Selected attachments",
    uploadProgress: "Uploading",
    forward: "Forward",
    forwardMessageTitle: "Forward message",
    forwardMessageDescription:
      "Choose one or more organization members. Each member receives this message in their direct conversation.",
    chooseRecipients: "Choose recipients",
    forwardSelected: "Forward to selected members",
    forwarding: "Forwarding...",
    forwardedSuccess: "Message forwarded successfully.",
    forwardedLabel: "Forwarded",
    forwardPartial: "The message was forwarded to some members, but not all.",
    selectRecipient: "Choose at least one member.",
    startCall: "Start video call",
    startAudioCall: "Start audio call",
    startVideoCall: "Start video call",
    scheduleCall: "Schedule call",
    scheduledCalls: "Organization call links",
    scheduledCallDescription:
      "Every active member may create a link. The link identifies the call; organization membership is still checked whenever it is opened or joined.",
    callLinkTitle: "Call title",
    callLinkTitlePlaceholder: "For example: Weekly operations review",
    callLinkDateTime: "Date and time",
    callLinkDuration: "Duration (minutes)",
    callLinkMaximum: "Maximum participants",
    callLinkMemberLimit: "Cannot exceed {count} active organization members.",
    callLinkMedia: "Call type",
    createCallLink: "Create call link",
    creatingCallLink: "Creating link…",
    copyCallLink: "Copy link",
    callLinkCopied: "Call link copied.",
    joinScheduledCall: "Open call",
    cancelScheduledCall: "Cancel link",
    noScheduledCalls: "No organization call links yet.",
    scheduledFor: "Scheduled",
    callRecordings: "Call recordings",
    viewRecordings: "Recordings",
    noCallRecordings: "No completed recording is available for this call.",
    recordingRetentionNotice:
      "Recordings are private, audited, and removed under the organization retention policy unless a legal hold applies.",
    downloadRecording: "Download audio",
    loadingRecordings: "Loading recordings…",
    downloadingRecording: "Downloading audio…",
    joinCall: "Join call",
    returnToCall: "Return to call",
    callEnded: "Call ended",
    callAlreadyActive: "Leave your current call before joining another call.",
    joining: "Joining...",
    starting: "Starting...",
    online: "Online",
    offline: "Offline",
    in_call: "In call",
    unavailableTitle: "Projects & Team is unavailable",
    unavailableDescription:
      "This workspace is only available to active Business or Enterprise organization members.",
    loading: "Loading workspace...",
    directMessage: "Direct message",
    groupChat: "Group chat",
    memberChat: "Member chat",
    noConversations: "No conversations yet.",
    noMessagesOrCalls: "No messages or call logs yet.",
    searchMessages: "Search messages",
    searchPlaceholder: "Search team messages...",
    searchingMessages: "Searching...",
    noSearchResults: "No matching messages.",
    unreadMessages: "Unread messages",
    creating: "Creating...",
    opening: "Opening...",
    noGroupYet: "No group chat yet.",
  },
  fr: {
    title: "Projets & équipe",
    subtitle:
      "Collaborez avec votre organisation grâce aux messages, fichiers partagés, espaces de groupe et appels vidéo.",
    backToDashboard: "Retour au tableau de bord",
    businessChats: "Discussions Business",
    back: "Retour",
    settings: "Paramètres",
    enableNotifications: "Activer les notifications",
    disableNotifications: "Désactiver les notifications",
    notificationsEnabled: "Notifications activées",
    notificationsDisabled: "Notifications désactivées",
    enablingNotifications: "Activation…",
    disablingNotifications: "Désactivation…",
    sendDocument: "Envoyer un document",
    sendDocumentDescription:
      "Choisissez un membre du forfait et jusqu’à 50 documents à partager.",
    chooseRecipient: "Choisir un destinataire",
    chooseDocument: "Choisir les documents",
    cancelDocument: "Annuler",
    preparingDocument: "Ouverture de la discussion...",
    inviteMembersTitle: "Invitez des membres pour envoyer des documents",
    inviteMembersDescription:
      "Ajoutez un autre membre à ce forfait Business ou Enterprise avant de partager des documents.",
    contactAdminDescription:
      "Demandez à un propriétaire ou administrateur d’inviter un autre membre avant de partager des documents.",
    inviteMembers: "Inviter des membres",
    businessGroupChat: "Discussion de groupe Business",
    subgroups: "Sous-groupes",
    createSubgroup: "Nouveau sous-groupe",
    createSubgroupTitle: "Créer un sous-groupe",
    subgroupDescription:
      "Sélectionnez les membres, puis échangez des messages, appelez et partagez des pièces jointes uniquement avec ce groupe.",
    subgroupName: "Nom du sous-groupe",
    subgroupNamePlaceholder: "Par exemple : Lancement produit",
    subgroupMembers: "Choisir les membres",
    subgroupMemberLimit: "Jusqu’à {count} membres, vous compris",
    subgroupMinimum: "Choisissez au moins deux autres membres.",
    subgroupLimitReached: "La limite de membres du forfait est atteinte.",
    createSubgroupChat: "Créer et ouvrir la discussion",
    createSubgroupAudioCall: "Créer et démarrer l’appel audio",
    createSubgroupVideoCall: "Créer et démarrer l’appel vidéo",
    creatingSubgroup: "Création du sous-groupe…",
    cancel: "Annuler",
    refresh: "Actualiser",
    teamMembers: "Membres de l’équipe",
    message: "Message",
    call: "Appel",
    audioCall: "Appel audio",
    videoCall: "Appel vidéo",
    you: "Vous",
    recentlyJoined: "Récemment rejoint",
    groupWorkspace: "Groupe de l’équipe",
    createGroupChat: "Créer le groupe",
    openGroupChat: "Ouvrir le groupe",
    callGroup: "Appel vidéo de groupe",
    ownerOnlyGroup:
      "Seul le propriétaire de l’organisation peut créer le groupe de l’équipe.",
    noMembers: "Aucun autre membre actif pour le moment.",
    messages: "Messages",
    chooseConversation:
      "Choisissez un membre ou le groupe de l’équipe pour commencer.",
    messagePlaceholder: "Écrire un message...",
    send: "Envoyer",
    sending: "Envoi...",
    realtimeConnecting: "Connexion...",
    messagePending: "Envoi...",
    messageFailed: "Échec de l’envoi",
    attachFile: "Joindre un fichier",
    removeAttachment: "Retirer la pièce jointe",
    selectedAttachment: "Pièce jointe sélectionnée",
    uploadingAttachment: "Téléversement...",
    openAttachment: "Ouvrir la pièce jointe",
    attachmentTooLarge:
      "La pièce jointe est trop volumineuse. Taille maximale : 20 Mo.",
    attachmentUnsupported:
      "Ce type de fichier n’est pas autorisé pour la messagerie d’équipe sécurisée.",
    attachmentSecured: "Analysé contre les logiciels malveillants et chiffré",
    attachmentUnavailable:
      "Cette ancienne pièce jointe est verrouillée jusqu’à la fin de sa migration de sécurité.",
    attachmentFailed: "Impossible d’envoyer les pièces jointes.",
    attachmentTooMany: "Un message peut contenir au maximum 50 pièces jointes.",
    attachmentMessageTooLarge: "La taille totale des pièces jointes dépasse 1000 Mo.",
    selectedAttachments: "Pièces jointes sélectionnées",
    uploadProgress: "Téléversement",
    forward: "Transférer",
    forwardMessageTitle: "Transférer le message",
    forwardMessageDescription:
      "Choisissez un ou plusieurs membres. Chaque membre recevra ce message dans sa conversation directe.",
    chooseRecipients: "Choisir les destinataires",
    forwardSelected: "Transférer aux membres sélectionnés",
    forwarding: "Transfert...",
    forwardedSuccess: "Message transféré avec succès.",
    forwardedLabel: "Transféré",
    forwardPartial: "Le message a été transféré à certains membres, mais pas à tous.",
    selectRecipient: "Choisissez au moins un membre.",
    startCall: "Démarrer l’appel vidéo",
    startAudioCall: "Démarrer un appel audio",
    startVideoCall: "Démarrer un appel vidéo",
    scheduleCall: "Planifier un appel",
    scheduledCalls: "Liens d’appel de l’organisation",
    scheduledCallDescription:
      "Chaque membre actif peut créer un lien. Le lien identifie l’appel; l’appartenance à l’organisation est toujours vérifiée à l’ouverture et à la connexion.",
    callLinkTitle: "Titre de l’appel",
    callLinkTitlePlaceholder: "Par exemple : Revue hebdomadaire des opérations",
    callLinkDateTime: "Date et heure",
    callLinkDuration: "Durée (minutes)",
    callLinkMaximum: "Participants maximum",
    callLinkMemberLimit: "Ne peut pas dépasser {count} membres actifs.",
    callLinkMedia: "Type d’appel",
    createCallLink: "Créer le lien d’appel",
    creatingCallLink: "Création du lien…",
    copyCallLink: "Copier le lien",
    callLinkCopied: "Lien d’appel copié.",
    joinScheduledCall: "Ouvrir l’appel",
    cancelScheduledCall: "Annuler le lien",
    noScheduledCalls: "Aucun lien d’appel d’organisation pour le moment.",
    scheduledFor: "Planifié",
    callRecordings: "Enregistrements de l’appel",
    viewRecordings: "Enregistrements",
    noCallRecordings:
      "Aucun enregistrement terminé n’est disponible pour cet appel.",
    recordingRetentionNotice:
      "Les enregistrements sont privés, audités et supprimés selon la politique de conservation de l’organisation, sauf obligation de conservation légale.",
    downloadRecording: "Télécharger l’audio",
    loadingRecordings: "Chargement des enregistrements…",
    downloadingRecording: "Téléchargement de l’audio…",
    joinCall: "Rejoindre l’appel",
    returnToCall: "Revenir à l’appel",
    callEnded: "Appel terminé",
    callAlreadyActive:
      "Quittez votre appel actuel avant de rejoindre un autre appel.",
    joining: "Connexion...",
    starting: "Démarrage...",
    online: "En ligne",
    offline: "Hors ligne",
    in_call: "En appel",
    unavailableTitle: "Projets & équipe indisponible",
    unavailableDescription:
      "Cet espace est réservé aux membres actifs d’une organisation Business ou Enterprise.",
    loading: "Chargement de l’espace...",
    directMessage: "Message direct",
    groupChat: "Groupe",
    memberChat: "Conversation membre",
    noConversations: "Aucune conversation pour le moment.",
    noMessagesOrCalls: "Aucun message ni journal d’appel pour le moment.",
    searchMessages: "Rechercher des messages",
    searchPlaceholder: "Rechercher dans les messages...",
    searchingMessages: "Recherche...",
    noSearchResults: "Aucun message correspondant.",
    unreadMessages: "Messages non lus",
    creating: "Création...",
    opening: "Ouverture...",
    noGroupYet: "Aucun groupe pour le moment.",
  },
};

export const pdfToolsLockActionTranslations = {
  en: {
    key: "lockPdf",
    name: "Lock PDF",
    description: "Protect a PDF with AES-256 password encryption and explicit permissions.",
    route: "/pdf-tools/lock",
    comingSoon: false,
  },
  fr: {
    key: "lockPdf",
    name: "Verrouiller un PDF",
    description:
      "Protégez un PDF avec un chiffrement AES-256 par mot de passe et des autorisations explicites.",
    route: "/pdf-tools/lock",
    comingSoon: false,
  },
};

export const lockPdfPageTranslations = {
  en: {
    back: "Back",
    badge: "PDF tools · Security",
    title: "Lock a PDF with a password",
    description:
      "Encrypt one PDF, or a paid-plan batch of PDFs, with AES-256 and choose the permissions available after the password is entered.",
    loading: "Checking account...",
    aes256Only: "AES-256 encryption",
    uploadTitle: "PDF files",
    uploadHelp:
      "Allowed: .pdf only. Each PDF can be up to 100 MB. The source PDF must not already be encrypted or password-protected.",
    chooseFiles: "Choose PDF file(s)",
    singleFileAccess:
      "Single-file Lock uses the existing PDF Tools guest or account quota.",
    batchAvailable: "Your plan supports up to {count} PDFs in one Lock batch.",
    batchFeatureLabel: "PDF locking",
    batchPaidOnly:
      "Batch PDF Lock is available only on Personal, Business, and Enterprise plans. Select one PDF or upgrade your plan.",
    noFile: "Choose a PDF file to lock.",
    passwordTitle: "Password",
    passwordLabel: "PDF password",
    confirmPasswordLabel: "Confirm PDF password",
    passwordHelp:
      "Enter the exact password that recipients will use to open the locked PDF. Spaces and Unicode characters are preserved exactly as entered.",
    passwordLengthRule: "Use {min} to {max} characters.",
    passwordCharacterCount: "{count} characters",
    passwordRequired: "Enter a password for the PDF.",
    passwordTooShort: "The PDF password must contain at least {min} characters.",
    passwordTooLong: "The PDF password must contain at most {max} characters.",
    passwordMismatch: "The password confirmation does not match.",
    showPassword: "Show password",
    hidePassword: "Hide password",
    securityOptionsTitle: "Encryption & permissions",
    securityOptionsHelp:
      "Permissions control what a recipient may do after successfully opening the PDF with the password.",
    encryptionLabel: "Encryption",
    encryptionValue: "AES-256",
    allowPrinting: "Allow printing",
    allowPrintingHelp: "Permit standard and high-quality printing after unlock.",
    allowCopying: "Allow copying",
    allowCopyingHelp: "Permit copying text and other extractable PDF content.",
    allowModifying: "Allow modifying",
    allowModifyingHelp: "Permit document modification after unlock.",
    allowAnnotations: "Allow annotations",
    allowAnnotationsHelp: "Permit comments and annotation changes after unlock.",
    allowFormFilling: "Allow form filling",
    allowFormFillingHelp: "Permit completing interactive form fields after unlock.",
    allowAccessibility: "Allow accessibility access",
    allowAccessibilityHelp:
      "Keep accessibility extraction available to assistive technologies. Enabled by default.",
    outputFilename: "Output filename",
    outputFilenameHelp:
      "ReDOCX derives the protected filename from the source file, for example report.locked.pdf.",
    batchOutputFilename: "Each source file → <source>.locked.pdf",
    batchOutputHelp:
      "Every file in the batch receives its own source-derived .locked.pdf filename.",
    lock: "Lock PDF",
    lockBatch: "Lock PDFs",
    locking: "Locking PDF...",
    resultTitle: "Locked PDF ready",
    resultDescription:
      "The generated PDF is password-protected with AES-256 using the permissions you selected.",
    resultFilename: "File",
    resultEncryption: "Protection",
    download: "Download locked PDF",
    share: "Share locked PDF",
    sharing: "Preparing share...",
    shareText:
      "Password-protected PDF from ReDOCX. Share the password separately through an appropriate channel.",
    shareUnavailable:
      "File sharing is not available in this browser. Download the locked PDF instead.",
    shareFailed:
      "The locked PDF could not be prepared for sharing. Download it and share the file manually.",
    passwordReminder:
      "Keep the password separately and provide it only to intended recipients. The downloaded PDF requires that password to open.",
    batchResultsTitle: "Batch Lock results",
    batchResultLabels: {
      succeeded: "succeeded",
      failed: "failed",
      plan: "plan",
      workers: "workers",
      processedSuccessfully: "Locked successfully.",
      fileFailed: "This PDF could not be locked.",
      downloadReady: "Locked PDF ready",
      downloadOutput: "Download locked PDF",
      resultReady: "Result ready",
      convertedOutput: "Locked output",
      noOutput:
        "Locking succeeded, but the response did not contain a downloadable artifact.",
      downloadableOutputs: "downloadable output",
      inlineResults: "inline result",
    },
  },
  fr: {
    back: "Retour",
    badge: "Outils PDF · Sécurité",
    title: "Verrouiller un PDF avec un mot de passe",
    description:
      "Chiffrez un PDF, ou un lot de PDF avec un forfait payant, en AES-256 et choisissez les autorisations disponibles après la saisie du mot de passe.",
    loading: "Vérification du compte...",
    aes256Only: "Chiffrement AES-256",
    uploadTitle: "Fichiers PDF",
    uploadHelp:
      "Autorisé : .pdf uniquement. Chaque PDF peut faire jusqu’à 100 Mo. Le PDF source ne doit pas déjà être chiffré ou protégé par mot de passe.",
    chooseFiles: "Choisir un ou plusieurs PDF",
    singleFileAccess:
      "Le verrouillage d’un seul fichier utilise le quota Outils PDF existant du visiteur ou du compte.",
    batchAvailable:
      "Votre forfait prend en charge jusqu’à {count} PDF dans un même lot de verrouillage.",
    batchFeatureLabel: "verrouillage PDF",
    batchPaidOnly:
      "Le verrouillage PDF par lot est réservé aux forfaits Personal, Business et Enterprise. Sélectionnez un seul PDF ou changez de forfait.",
    noFile: "Choisissez un fichier PDF à verrouiller.",
    passwordTitle: "Mot de passe",
    passwordLabel: "Mot de passe du PDF",
    confirmPasswordLabel: "Confirmer le mot de passe du PDF",
    passwordHelp:
      "Saisissez le mot de passe exact que les destinataires utiliseront pour ouvrir le PDF verrouillé. Les espaces et caractères Unicode sont conservés tels quels.",
    passwordLengthRule: "Utilisez de {min} à {max} caractères.",
    passwordCharacterCount: "{count} caractères",
    passwordRequired: "Saisissez un mot de passe pour le PDF.",
    passwordTooShort:
      "Le mot de passe du PDF doit contenir au moins {min} caractères.",
    passwordTooLong:
      "Le mot de passe du PDF doit contenir au maximum {max} caractères.",
    passwordMismatch: "La confirmation du mot de passe ne correspond pas.",
    showPassword: "Afficher le mot de passe",
    hidePassword: "Masquer le mot de passe",
    securityOptionsTitle: "Chiffrement et autorisations",
    securityOptionsHelp:
      "Les autorisations définissent ce qu’un destinataire peut faire après avoir ouvert le PDF avec le mot de passe.",
    encryptionLabel: "Chiffrement",
    encryptionValue: "AES-256",
    allowPrinting: "Autoriser l’impression",
    allowPrintingHelp:
      "Autoriser l’impression standard et haute qualité après déverrouillage.",
    allowCopying: "Autoriser la copie",
    allowCopyingHelp:
      "Autoriser la copie du texte et des autres contenus PDF extractibles.",
    allowModifying: "Autoriser les modifications",
    allowModifyingHelp: "Autoriser la modification du document après déverrouillage.",
    allowAnnotations: "Autoriser les annotations",
    allowAnnotationsHelp:
      "Autoriser les commentaires et modifications d’annotations après déverrouillage.",
    allowFormFilling: "Autoriser le remplissage des formulaires",
    allowFormFillingHelp:
      "Autoriser le remplissage des champs de formulaire interactifs après déverrouillage.",
    allowAccessibility: "Autoriser l’accès d’accessibilité",
    allowAccessibilityHelp:
      "Conserver l’extraction d’accessibilité pour les technologies d’assistance. Activé par défaut.",
    outputFilename: "Nom du fichier de sortie",
    outputFilenameHelp:
      "ReDOCX dérive le nom protégé du fichier source, par exemple rapport.locked.pdf.",
    batchOutputFilename: "Chaque source → <source>.locked.pdf",
    batchOutputHelp:
      "Chaque fichier du lot reçoit son propre nom .locked.pdf dérivé du fichier source.",
    lock: "Verrouiller le PDF",
    lockBatch: "Verrouiller les PDF",
    locking: "Verrouillage du PDF...",
    resultTitle: "PDF verrouillé prêt",
    resultDescription:
      "Le PDF généré est protégé par mot de passe avec AES-256 et les autorisations sélectionnées.",
    resultFilename: "Fichier",
    resultEncryption: "Protection",
    download: "Télécharger le PDF verrouillé",
    share: "Partager le PDF verrouillé",
    sharing: "Préparation du partage...",
    shareText:
      "PDF protégé par mot de passe depuis ReDOCX. Transmettez le mot de passe séparément par un canal approprié.",
    shareUnavailable:
      "Le partage de fichier n’est pas disponible dans ce navigateur. Téléchargez plutôt le PDF verrouillé.",
    shareFailed:
      "Le PDF verrouillé n’a pas pu être préparé pour le partage. Téléchargez-le et partagez-le manuellement.",
    passwordReminder:
      "Conservez le mot de passe séparément et transmettez-le uniquement aux destinataires prévus. Le PDF téléchargé exige ce mot de passe pour s’ouvrir.",
    batchResultsTitle: "Résultats du verrouillage par lot",
    batchResultLabels: {
      succeeded: "réussis",
      failed: "échoués",
      plan: "forfait",
      workers: "workers",
      processedSuccessfully: "Verrouillé avec succès.",
      fileFailed: "Ce PDF n’a pas pu être verrouillé.",
      downloadReady: "PDF verrouillé prêt",
      downloadOutput: "Télécharger le PDF verrouillé",
      resultReady: "Résultat prêt",
      convertedOutput: "Sortie verrouillée",
      noOutput:
        "Le verrouillage a réussi, mais la réponse ne contient aucun fichier téléchargeable.",
      downloadableOutputs: "sortie téléchargeable",
      inlineResults: "résultat intégré",
    },
  },
};

export const dataProtectionSensitiveLabelTranslations = {
  en: {
    name: "Name",
    email_address: "Email address",
    phone_number: "Phone number",
    account_number: "Account number",
    card_number: "Card number",
    national_id: "National / government ID (SSN, SIN, NIN)",
    tax_id: "Tax ID",
    passport_number: "Passport number",
    contact_address: "Contact address",
    date_of_birth: "Date of birth",
    age: "Age",
    signature: "Signature",
    custom_mask: "Custom mask",
  },
  fr: {
    name: "Nom",
    email_address: "Adresse e-mail",
    phone_number: "Numéro de téléphone",
    account_number: "Numéro de compte",
    card_number: "Numéro de carte",
    national_id: "Identifiant national / gouvernemental (SSN, SIN, NIN)",
    tax_id: "Identifiant fiscal",
    passport_number: "Numéro de passeport",
    contact_address: "Adresse de contact",
    date_of_birth: "Date de naissance",
    age: "Âge",
    signature: "Signature",
    custom_mask: "Masquage personnalisé",
  },
};

export const grammarPageTranslations = {
  en: {
    badge: "Grammar correction",
    title: "Correct grammar while preserving the original meaning",
    description:
      "Upload a PDF or Word document, or paste inline text. The correction keeps the original tone, structure, and intent while fixing grammar and syntax.",
    fileMode: "Upload file",
    textMode: "Inline text",
    uploadTitle: "Upload content to correct",
    allowedFileInputs:
      "Allowed: .pdf and .docx. Rejected automatically: .png, .jpg, .jpeg, and unsupported formats.",
    outputExtensionWillBe: "Output extension will be",
    pasteTextLabel: "Paste text to correct",
    pasteTextPlaceholder: "Paste or type the text you want to correct...",
    inlineTextTreatedAs:
      "Inline text is treated as .txt, so the output extension will also be .txt.",
    unsupportedFileType:
      "Unsupported file type: {ext}. Only .pdf and .docx uploads are allowed. PNG, JPG, JPEG and other image formats are rejected.",
    fileTooLarge: "File is too large. Maximum allowed size is {maxSize} MB.",
    correctionFailed: "Something went wrong while correcting the grammar.",
    correctingGrammar: "Correcting grammar...",
    grammarCorrect: "Correct grammar",
    formatPolicy: "Format policy",
    policySubtitle: "Grammar fixes without rewriting intent",
    allowedUploadsLabel: "Allowed uploads:",
    inlineInputLabel: "Inline input:",
    rejectedAutomaticallyLabel: "Rejected automatically:",
    outputRuleLabel: "Output rule:",
    inlineInputValue: "treated as .txt",
    rejectedAutomaticallyValue: ".png, .jpg, .jpeg, and unsupported file types",
    outputRuleValue: "output extension mirrors the input extension",
    correctionOutputTitle: "Corrected output",
    previewEmpty:
      "Your corrected text or downloadable file details will appear here after processing.",
    outputExtensionLabel: "Output extension:",
    downloadCorrectedFile: "Download corrected file",
  },
  fr: {
    badge: "Correction grammaticale",
    title: "Corriger la grammaire tout en préservant le sens d’origine",
    description:
      "Importez un document PDF ou Word, ou collez du texte. La correction préserve le ton, la structure et l’intention d’origine tout en corrigeant la grammaire et la syntaxe.",
    fileMode: "Importer un fichier",
    textMode: "Texte saisi",
    uploadTitle: "Importer le contenu à corriger",
    allowedFileInputs:
      "Autorisés : .pdf et .docx. Rejetés automatiquement : .png, .jpg, .jpeg et les formats non pris en charge.",
    outputExtensionWillBe: "L’extension du fichier de sortie sera",
    pasteTextLabel: "Collez le texte à corriger",
    pasteTextPlaceholder: "Collez ou saisissez le texte à corriger...",
    inlineTextTreatedAs:
      "Le texte saisi est traité comme un fichier .txt ; l’extension de sortie sera donc également .txt.",
    unsupportedFileType:
      "Type de fichier non pris en charge : {ext}. Seuls les fichiers .pdf et .docx peuvent être importés. Les formats PNG, JPG, JPEG et les autres formats d’image sont refusés.",
    fileTooLarge: "Le fichier est trop volumineux. La taille maximale autorisée est de {maxSize} Mo.",
    correctionFailed: "Une erreur est survenue pendant la correction grammaticale.",
    correctingGrammar: "Correction grammaticale en cours...",
    grammarCorrect: "Corriger la grammaire",
    formatPolicy: "Règles de format",
    policySubtitle: "Corrections grammaticales sans réécriture de l’intention",
    allowedUploadsLabel: "Fichiers autorisés :",
    inlineInputLabel: "Texte saisi :",
    rejectedAutomaticallyLabel: "Rejetés automatiquement :",
    outputRuleLabel: "Règle de sortie :",
    inlineInputValue: "traité comme un fichier .txt",
    rejectedAutomaticallyValue: ".png, .jpg, .jpeg et types de fichiers non pris en charge",
    outputRuleValue: "l’extension de sortie reprend celle de l’entrée",
    correctionOutputTitle: "Texte corrigé",
    previewEmpty:
      "Le texte corrigé ou les informations du fichier téléchargeable apparaîtront ici après le traitement.",
    outputExtensionLabel: "Extension de sortie :",
    downloadCorrectedFile: "Télécharger le fichier corrigé",
  },
};

export const completedEnvelopePageTranslations = {
  en: {
    secureSign: "Secure ReDOCX Sign",
    loading: "Loading completed envelope...",
    completeTitle: "Signing is complete",
    envelopeLabel: "Envelope",
    downloadHelp:
      "This secure link lets you download the final signed PDF and its audit certificate.",
    downloadSignedPdf: "Download signed PDF",
    downloadSignedEnvelope: "Download signed envelope",
    downloadCertificate: "Download certificate",
  },
  fr: {
    secureSign: "Signature ReDOCX sécurisée",
    loading: "Chargement de l’enveloppe finalisée...",
    completeTitle: "La signature est terminée",
    envelopeLabel: "Enveloppe",
    downloadHelp:
      "Ce lien sécurisé vous permet de télécharger le PDF signé final ainsi que son certificat d’audit.",
    downloadSignedPdf: "Télécharger le PDF signé",
    downloadSignedEnvelope: "Télécharger l’enveloppe signée",
    downloadCertificate: "Télécharger le certificat",
  },
};

export const recipientSigningPageTranslations = {
  en: {
    title: "Review and sign document",
    loading: "Loading your secure signing request...",
    invalid: "This signing link is invalid or no longer available.",
    document: "Document",
    envelopeDocuments: "Envelope documents",
    reviewEveryDocument: "Review every document in the envelope before signing.",
    fields: "Complete the requested fields",
    signature: "Your signature",
    initials: "Your initials",
    signatureHelp: "Type your full name as your electronic signature.",
    initialsHelp: "Use the initials you want placed in initials boxes.",
    instructions:
      "Review the document, complete the requested fields, confirm your consent, then select Sign document.",
    requiredField: "Complete all required fields before signing.",
    requiredCheckbox: "Please check every required confirmation box.",
    consent:
      "I have reviewed the document and agree to use this electronic signature.",
    submit: "Sign document",
    signing: "Applying signature...",
    completed: "Your signature was applied successfully.",
    completedHelp:
      "You are finished. The sender will be able to continue the signing process, and you can safely close this page.",
    secureSign: "Secure ReDOCX Sign",
    requestedFor: "Requested for",
    autoFilled: "filled automatically by ReDOCX",
    noAssignedSignature: "No signature or initials field is assigned to you.",
    enterLegalSignature: "Enter your legal signature.",
    enterInitials: "Enter your initials.",
    consentRequired: "You must accept the electronic-signature consent statement.",
    fieldLabels: {
      name: "Full name",
      email: "Email address",
      date_signed: "Date signed",
      text: "Text",
      checkbox: "Confirmation",
      signature: "Signature",
      initials: "Initials",
      fallback: "Field",
    },
  },
  fr: {
    title: "Vérifier et signer le document",
    loading: "Chargement de votre demande de signature sécurisée...",
    invalid: "Ce lien de signature est invalide ou n’est plus disponible.",
    document: "Document",
    envelopeDocuments: "Documents de l’enveloppe",
    reviewEveryDocument: "Vérifiez chaque document de l’enveloppe avant de signer.",
    fields: "Renseignez les champs demandés",
    signature: "Votre signature",
    initials: "Vos initiales",
    signatureHelp: "Saisissez votre nom complet comme signature électronique.",
    initialsHelp: "Saisissez les initiales à placer dans les champs prévus.",
    instructions:
      "Vérifiez le document, renseignez les champs demandés, confirmez votre consentement, puis sélectionnez Signer le document.",
    requiredField: "Renseignez tous les champs obligatoires avant de signer.",
    requiredCheckbox: "Cochez toutes les cases de confirmation obligatoires.",
    consent:
      "J’ai vérifié le document et j’accepte d’utiliser cette signature électronique.",
    submit: "Signer le document",
    signing: "Application de la signature...",
    completed: "Votre signature a été appliquée avec succès.",
    completedHelp:
      "Vous avez terminé. L’expéditeur pourra poursuivre le processus de signature et vous pouvez fermer cette page en toute sécurité.",
    secureSign: "Signature ReDOCX sécurisée",
    requestedFor: "Demandé pour",
    autoFilled: "renseigné automatiquement par ReDOCX",
    noAssignedSignature: "Aucun champ de signature ou d’initiales ne vous est attribué.",
    enterLegalSignature: "Saisissez votre signature légale.",
    enterInitials: "Saisissez vos initiales.",
    consentRequired: "Vous devez accepter la déclaration de consentement à la signature électronique.",
    fieldLabels: {
      name: "Nom complet",
      email: "Adresse e-mail",
      date_signed: "Date de signature",
      text: "Texte",
      checkbox: "Confirmation",
      signature: "Signature",
      initials: "Initiales",
      fallback: "Champ",
    },
  },
};

export const legacyTeamMessagesPageTranslations = {
  en: { opening: "Opening Projects & Team…" },
  fr: { opening: "Ouverture de Projets & équipe…" },
};




// Centralized user-facing error translations. Canonical codes mirror backend/errors.py.
export const errorTranslations = {
  en: {
      INPUT_REQUIRED: "Provide the required input and try again.",
      INVALID_REQUEST: "Some request information is invalid. Please review it and try again.",
      INVALID_MEDIA_DURATION: "The media duration could not be validated or exceeds the allowed limit.",
      INVALID_ACTION: "This action is not supported.",
      INVALID_UPLOAD_METADATA: "The uploaded file is missing required information. Please choose the file again.",
      INVALID_FILE_ENCODING: "This text file must use UTF-8 encoding.",
      UNSUPPORTED_FILE_TYPE: "That file type is not supported for this feature.",
      UNSUPPORTED_CONVERSION_PAIR: "That conversion is not supported.",
      UNSUPPORTED_OUTPUT_FORMAT: "That output format is not supported.",
      FILE_EMPTY: "The selected file is empty.",
      FILE_TOO_LARGE: "The file is larger than the allowed limit for this feature.",
      PASSWORD_PROTECTED_FILE: "Password-protected or encrypted files are not supported for this operation. Use an unlocked file.",
      UNSAFE_FILE: "This file could not be accepted safely. Check the file and try a trusted copy.",
      MALWARE_DETECTED: "This file was blocked because it failed the malware safety check.",
      UPLOAD_SECURITY_UNAVAILABLE: "Secure file checking is temporarily unavailable. Please try again later.",
      EXTRACTION_FAILED: "We couldn't read usable content from this file.",
      SOURCE_FILE_NOT_FOUND: "The required file is no longer available. Please upload it again.",
      UPLOAD_PERSIST_FAILED: "We couldn't save the uploaded file. Please try again.",
      RESOURCE_NOT_FOUND: "The requested item could not be found.",
      RESOURCE_GONE: "This item is no longer available.",
      REQUEST_CONFLICT: "This action can't be completed in the item's current state. Refresh and try again if appropriate.",
      REQUEST_TOO_EARLY: "This action is not available yet.",
      METHOD_NOT_ALLOWED: "This operation is not available for the requested action.",
      PAYLOAD_TOO_LARGE: "The request is larger than the allowed limit.",
      PROCESSING_FAILED: "We couldn't complete processing for this request.",
      PROCESSING_OUTPUT_MISSING: "Processing finished, but the expected output could not be prepared.",
      PROCESSING_RESOURCE_LIMIT: "This file could not be processed within the available resource limits.",
      PREVIEW_UNAVAILABLE: "The preview is temporarily unavailable.",
      WORKFLOW_PREREQUISITE_REQUIRED: "Complete the required previous step before continuing.",
      FEATURE_NOT_AVAILABLE: "This feature isn't available for the current account or plan.",
      FEATURE_NOT_CONFIGURED: "This feature is temporarily unavailable.",
      RATE_LIMIT_EXCEEDED: "You've reached a usage limit for this operation. Please try again later.",
      RATE_LIMIT_UNAVAILABLE: "We can't verify usage limits right now. Please try again later.",
      PLAN_REQUIRED: "This feature requires an eligible ReDOCX plan.",
      PLAN_LIMIT_EXCEEDED: "The current account or plan limit has been reached.",
      AUTHORIZATION_REQUIRED: "Please sign in to continue.",
      INVALID_TOKEN: "Your session is invalid or expired. Please sign in again.",
      PERMISSION_DENIED: "You don't have permission to perform this action.",
      INSUFFICIENT_SCOPE: "You don't have permission to use this feature.",
      AUTH_PROVIDER_UNAVAILABLE: "Account authentication services are temporarily unavailable. Please try again later.",
      ACCOUNT_DELETED: "This account is no longer available.",
      ACCOUNT_DEACTIVATED: "This account is currently deactivated. Restore it before continuing, if restoration is still available.",
      ACCOUNT_RECOVERY_EXPIRED: "The account restoration window has ended.",
      ACCOUNT_OPERATION_FAILED: "We couldn't complete the account request. Please try again.",
      BILLING_UNAVAILABLE: "Billing is temporarily unavailable. Please try again before starting another payment.",
      BILLING_CONFLICT: "This billing change can't be completed in the current subscription state.",
      SUBSCRIPTION_PERIOD_LOCKED: "Your paid plan cannot be changed until the current paid period ends. You can still cancel or resume renewal.",
      PAYMENT_PENDING: "Payment confirmation is still pending. Please don't make another payment; try the confirmation again shortly.",
      PAYMENT_VERIFICATION_FAILED: "We couldn't verify this payment safely. No entitlement was changed. Please retry verification or contact support if you were charged.",
      SUBSCRIPTION_OPERATION_FAILED: "We couldn't update the subscription safely. Please try again; don't start a second subscription.",
      INVALID_WEBHOOK: "The payment notification could not be verified.",
      ORGANIZATION_ACCESS_DENIED: "You no longer have access to this organization or conversation.",
      ORGANIZATION_PERMISSION_REQUIRED: "Your organization role doesn't permit this action.",
      ORGANIZATION_SEAT_LIMIT_REACHED: "The organization has reached the account limit for its current plan.",
      TEAM_SERVICE_UNAVAILABLE: "This team service is temporarily unavailable. Please try again later.",
      CONVERSATION_NOT_FOUND: "This conversation is no longer available.",
      ATTACHMENT_INVALID: "This attachment could not be accepted safely.",
      ATTACHMENT_NOT_FOUND: "This attachment is no longer available.",
      ATTACHMENT_TOO_LARGE: "This attachment is larger than the allowed limit.",
      ATTACHMENT_QUOTA_EXCEEDED: "The organization has reached its secure attachment storage limit.",
      ATTACHMENT_SECURITY_UNAVAILABLE: "Secure attachment processing is temporarily unavailable. Please try again later.",
      ATTACHMENT_INTEGRITY_FAILED: "This attachment could not be verified safely and cannot be opened.",
      BATCH_UPLOAD_INVALID: "The selected batch does not meet this feature's batch requirements.",
      BATCH_UPLOAD_PLAN_REQUIRED: "Batch processing requires an eligible paid plan.",
      DUPLICATE_UPLOAD: "One or more selected files duplicate another file in this batch.",
      CALL_NOT_FOUND: "This call is no longer available.",
      CALL_ACCESS_DENIED: "You don't have permission to perform this call action.",
      CALL_STATE_CONFLICT: "This call action isn't available in the call's current state.",
      CALL_RECORDING_CONSENT_REQUIRED: "Recording can start only after the required participant consent is recorded.",
      UPSTREAM_TIMEOUT: "The processing service took too long to respond. Please try again.",
      UPSTREAM_SERVICE_ERROR: "A processing service could not complete the request. Please try again.",
      SERVICE_UNAVAILABLE: "This service is temporarily unavailable. Please try again later.",
      INTERNAL_ERROR: "We couldn't complete the request.",
      INPUT_REQUIRED_ADD_FILE_OR_TEXT: "Add a file or enter text to continue.",
      BILLING_SCHEMA_UNAVAILABLE: "Billing is temporarily unavailable. No payment was started. Please try again later.",
      PRESENCE_PROVIDER_MANAGED: "Call presence is managed automatically and can't be changed manually.",
      INPUT_FILE_REQUIRED: "Choose the required file to continue.",
      INLINE_TEXT_REQUIRED: "Enter some text to continue.",
      QUESTIONS_REQUIRED_BEFORE_ANSWERS: "Generate questions first, then generate answers.",
      PDF_REGION_NO_MATCH: "No matching content was found in the selected PDF region.",
      OCR_UNAVAILABLE: "OCR is temporarily unavailable.",
      OCR_LANGUAGE_INVALID: "One of the OCR language settings is invalid.",
      SINGLE_INPUT_REQUIRED: "Provide one input source only and try again.",
      REALTIME_ACTION_UNSUPPORTED: "That realtime action is not supported.",
      REALTIME_MESSAGE_FAILED: "The realtime message could not be processed.",
      SIGNATURE_AREA_TOO_SMALL: "The selected signature area is too small. Choose a larger area and try again.",
      SIGNING_SERVICE_UNAVAILABLE: "The signing service is temporarily unavailable.",
      ATTACHMENT_SECURITY_PENDING: "This attachment is temporarily unavailable while its security state is being verified.",
      COMPLETION_LINK_INVALID: "This completion link is invalid or no longer available.",
      COMPLETED_ENVELOPE_LOAD_FAILED: "Could not load the completed envelope.",
      COMPLETED_ENVELOPE_DOWNLOAD_FAILED: "Could not download the completed file.",
      SIGNING_LINK_INVALID: "This signing link is invalid or no longer available.",
      SIGNATURE_APPLY_FAILED: "Could not apply your signature.",
      OUTPUT_ARTIFACT_REQUEST_FAILED: "Could not retrieve the processed output.",
      BROWSER_FILE_PREPARE_UNAVAILABLE: "This browser cannot prepare files for sharing.",
      OUTPUT_FILE_UNAVAILABLE: "The output file is not available.",
      OUTPUT_FILE_EMPTY: "The output file is empty.",
      OUTPUT_SHARE_PREPARE_FAILED: "Could not prepare the output for sharing.",
      ORGANIZATION_MEMBERS_LOAD_FAILED: "Could not load organization members for sharing.",
      SECURE_CONVERSATION_RESOLVE_FAILED: "Could not resolve the secure ReDOCX conversation.",
      SECURE_ATTACHMENT_REFERENCE_MISSING: "The secure attachment was sent but no message reference was returned.",
      DELIVERY_CONFIRMATION_FAILED: "The remaining deliveries could not be confirmed.",
      SECURE_SHARE_FAILED: "Could not share the output securely.",
      BACKEND_RESULT_MISSING: "The backend returned no result.",
      BACKEND_RESPONSE_INVALID: "The backend returned an unexpected response.",
      DOCUMENT_TYPE_UNSUPPORTED: "The document type detector returned an unsupported type.",
      MEDIA_DURATION_READ_FAILED: "Could not read the media duration.",
      NO_SPEECH_DETECTED: "No intelligible speech was detected. Speak clearly or try a recording with audible speech.",
      BATCH_MEDIA_TYPE_MISMATCH: "All files in a speech-to-text batch must use the same media type.",
      TRANSCRIPT_TEXT_MISSING: "The backend returned no transcript text.",
      TRANSCRIPT_ARTIFACT_MISSING: "The transcript was generated, but no PDF download file was returned.",
      TRANSCRIPT_SUBTITLES_MISSING: "The transcript was generated, but synchronized subtitle timing or playback media was not returned.",
      RECORDING_DOWNLOAD_FAILED: "Could not download the call recording.",
      SUBGROUP_CREATE_FAILED: "Could not create the subgroup conversation.",
      MESSAGE_FORWARD_FAILED: "Could not forward the message.",
      COMPRESSION_JOB_TIMEOUT: "Compression has been processing for too long. Keep the job ID and try the status request again.",
      COMPRESSION_RESULT_MISSING: "Compression completed without a downloadable result.",
      COMPRESSION_FAILED: "PDF compression failed.",
      COMPRESSION_JOB_STATUS_FAILED: "Could not read compression job status.",
      SIGNATURE_IMAGE_PREPARE_FAILED: "Could not prepare the signature image.",
      CONVERTED_OUTPUT_UNAVAILABLE: "The converted output is not available.",
      CONVERTED_OUTPUT_EMPTY: "The converted output is empty.",
      CONVERTED_SHARE_PREPARE_FAILED: "Could not prepare the converted file for sharing.",
      CONVERTED_SECURE_SHARE_FAILED: "Could not share the converted file securely.",
      TEAM_REQUEST_FAILED: "The team request could not be completed.",
      PRINT_ARCHIVE_UNSUPPORTED: "ZIP packages cannot be printed directly. Download and extract the package, then print the required document.",
      PRINT_UNSUPPORTED: "This output format cannot be printed directly. Download the file and open it in an application that supports printing.",
      PRINT_FAILED: "Could not prepare this output for printing.",
  },
  fr: {
      INPUT_REQUIRED: "Renseignez l’entrée requise puis réessayez.",
      INVALID_REQUEST: "Certaines informations de la requête sont invalides. Vérifiez-les puis réessayez.",
      INVALID_MEDIA_DURATION: "La durée du média n’a pas pu être validée ou dépasse la limite autorisée.",
      INVALID_ACTION: "Cette action n’est pas prise en charge.",
      INVALID_UPLOAD_METADATA: "Le fichier importé ne contient pas toutes les informations requises. Sélectionnez-le de nouveau.",
      INVALID_FILE_ENCODING: "Ce fichier texte doit être encodé en UTF-8.",
      UNSUPPORTED_FILE_TYPE: "Ce type de fichier n’est pas pris en charge pour cette fonctionnalité.",
      UNSUPPORTED_CONVERSION_PAIR: "Cette conversion n’est pas prise en charge.",
      UNSUPPORTED_OUTPUT_FORMAT: "Ce format de sortie n’est pas pris en charge.",
      FILE_EMPTY: "Le fichier sélectionné est vide.",
      FILE_TOO_LARGE: "Le fichier dépasse la taille autorisée pour cette fonctionnalité.",
      PASSWORD_PROTECTED_FILE: "Les fichiers protégés par mot de passe ou chiffrés ne sont pas pris en charge pour cette opération. Utilisez un fichier déverrouillé.",
      UNSAFE_FILE: "Ce fichier n’a pas pu être accepté en toute sécurité. Vérifiez-le et réessayez avec une copie fiable.",
      MALWARE_DETECTED: "Ce fichier a été bloqué car il n’a pas satisfait au contrôle de sécurité contre les logiciels malveillants.",
      UPLOAD_SECURITY_UNAVAILABLE: "Le contrôle sécurisé des fichiers est temporairement indisponible. Réessayez plus tard.",
      EXTRACTION_FAILED: "Nous n’avons pas pu extraire de contenu exploitable de ce fichier.",
      SOURCE_FILE_NOT_FOUND: "Le fichier requis n’est plus disponible. Importez-le de nouveau.",
      UPLOAD_PERSIST_FAILED: "Nous n’avons pas pu enregistrer le fichier importé. Réessayez.",
      RESOURCE_NOT_FOUND: "L’élément demandé est introuvable.",
      RESOURCE_GONE: "Cet élément n’est plus disponible.",
      REQUEST_CONFLICT: "Cette action ne peut pas être effectuée dans l’état actuel de l’élément. Actualisez la page et réessayez si nécessaire.",
      REQUEST_TOO_EARLY: "Cette action n’est pas encore disponible.",
      METHOD_NOT_ALLOWED: "Cette opération n’est pas disponible pour l’action demandée.",
      PAYLOAD_TOO_LARGE: "La requête dépasse la taille autorisée.",
      PROCESSING_FAILED: "Nous n’avons pas pu terminer le traitement de cette requête.",
      PROCESSING_OUTPUT_MISSING: "Le traitement est terminé, mais la sortie attendue n’a pas pu être préparée.",
      PROCESSING_RESOURCE_LIMIT: "Ce fichier n’a pas pu être traité avec les ressources disponibles.",
      PREVIEW_UNAVAILABLE: "L’aperçu est temporairement indisponible.",
      WORKFLOW_PREREQUISITE_REQUIRED: "Terminez l’étape précédente requise avant de continuer.",
      FEATURE_NOT_AVAILABLE: "Cette fonctionnalité n’est pas disponible pour ce compte ou ce forfait.",
      FEATURE_NOT_CONFIGURED: "Cette fonctionnalité est temporairement indisponible.",
      RATE_LIMIT_EXCEEDED: "Vous avez atteint une limite d’utilisation pour cette opération. Réessayez plus tard.",
      RATE_LIMIT_UNAVAILABLE: "Nous ne pouvons pas vérifier les limites d’utilisation pour le moment. Réessayez plus tard.",
      PLAN_REQUIRED: "Cette fonctionnalité nécessite un forfait ReDOCX éligible.",
      PLAN_LIMIT_EXCEEDED: "La limite actuelle du compte ou du forfait a été atteinte.",
      AUTHORIZATION_REQUIRED: "Connectez-vous pour continuer.",
      INVALID_TOKEN: "Votre session est invalide ou a expiré. Reconnectez-vous.",
      PERMISSION_DENIED: "Vous n’avez pas l’autorisation d’effectuer cette action.",
      INSUFFICIENT_SCOPE: "Vous n’avez pas l’autorisation d’utiliser cette fonctionnalité.",
      AUTH_PROVIDER_UNAVAILABLE: "Les services d’authentification du compte sont temporairement indisponibles. Réessayez plus tard.",
      ACCOUNT_DELETED: "Ce compte n’est plus disponible.",
      ACCOUNT_DEACTIVATED: "Ce compte est actuellement désactivé. Restaurez-le avant de continuer si la restauration est encore possible.",
      ACCOUNT_RECOVERY_EXPIRED: "La période de restauration du compte est terminée.",
      ACCOUNT_OPERATION_FAILED: "Nous n’avons pas pu terminer l’opération sur le compte. Réessayez.",
      BILLING_UNAVAILABLE: "La facturation est temporairement indisponible. Réessayez avant d’effectuer un autre paiement.",
      BILLING_CONFLICT: "Cette modification de facturation ne peut pas être effectuée dans l’état actuel de l’abonnement.",
      SUBSCRIPTION_PERIOD_LOCKED: "Votre forfait payant ne peut pas être modifié avant la fin de la période payée en cours. Vous pouvez toujours annuler ou reprendre le renouvellement.",
      PAYMENT_PENDING: "La confirmation du paiement est toujours en attente. N’effectuez pas un autre paiement ; réessayez la confirmation dans quelques instants.",
      PAYMENT_VERIFICATION_FAILED: "Nous n’avons pas pu vérifier ce paiement de manière sécurisée. Aucun droit d’accès n’a été modifié. Réessayez la vérification ou contactez le support si vous avez été débité.",
      SUBSCRIPTION_OPERATION_FAILED: "Nous n’avons pas pu mettre à jour l’abonnement de manière sécurisée. Réessayez et ne créez pas un second abonnement.",
      INVALID_WEBHOOK: "La notification de paiement n’a pas pu être vérifiée.",
      ORGANIZATION_ACCESS_DENIED: "Vous n’avez plus accès à cette organisation ou à cette conversation.",
      ORGANIZATION_PERMISSION_REQUIRED: "Votre rôle dans l’organisation n’autorise pas cette action.",
      ORGANIZATION_SEAT_LIMIT_REACHED: "L’organisation a atteint la limite de comptes de son forfait actuel.",
      TEAM_SERVICE_UNAVAILABLE: "Ce service d’équipe est temporairement indisponible. Réessayez plus tard.",
      CONVERSATION_NOT_FOUND: "Cette conversation n’est plus disponible.",
      ATTACHMENT_INVALID: "Cette pièce jointe n’a pas pu être acceptée en toute sécurité.",
      ATTACHMENT_NOT_FOUND: "Cette pièce jointe n’est plus disponible.",
      ATTACHMENT_TOO_LARGE: "Cette pièce jointe dépasse la taille autorisée.",
      ATTACHMENT_QUOTA_EXCEEDED: "L’organisation a atteint sa limite de stockage sécurisé pour les pièces jointes.",
      ATTACHMENT_SECURITY_UNAVAILABLE: "Le traitement sécurisé des pièces jointes est temporairement indisponible. Réessayez plus tard.",
      ATTACHMENT_INTEGRITY_FAILED: "Cette pièce jointe n’a pas pu être vérifiée en toute sécurité et ne peut pas être ouverte.",
      BATCH_UPLOAD_INVALID: "La sélection de fichiers ne respecte pas les exigences de traitement par lot de cette fonctionnalité.",
      BATCH_UPLOAD_PLAN_REQUIRED: "Le traitement par lot nécessite un forfait payant éligible.",
      DUPLICATE_UPLOAD: "Un ou plusieurs fichiers sélectionnés dupliquent un autre fichier de ce lot.",
      CALL_NOT_FOUND: "Cet appel n’est plus disponible.",
      CALL_ACCESS_DENIED: "Vous n’avez pas l’autorisation d’effectuer cette action d’appel.",
      CALL_STATE_CONFLICT: "Cette action n’est pas disponible dans l’état actuel de l’appel.",
      CALL_RECORDING_CONSENT_REQUIRED: "L’enregistrement ne peut commencer qu’après l’enregistrement du consentement requis des participants.",
      UPSTREAM_TIMEOUT: "Le service de traitement a mis trop de temps à répondre. Réessayez.",
      UPSTREAM_SERVICE_ERROR: "Un service de traitement n’a pas pu terminer la requête. Réessayez.",
      SERVICE_UNAVAILABLE: "Ce service est temporairement indisponible. Réessayez plus tard.",
      INTERNAL_ERROR: "Nous n’avons pas pu terminer la requête.",
      INPUT_REQUIRED_ADD_FILE_OR_TEXT: "Ajoutez un fichier ou saisissez du texte pour continuer.",
      BILLING_SCHEMA_UNAVAILABLE: "La facturation est temporairement indisponible. Aucun paiement n’a été lancé. Réessayez plus tard.",
      PRESENCE_PROVIDER_MANAGED: "La présence d’appel est gérée automatiquement et ne peut pas être modifiée manuellement.",
      INPUT_FILE_REQUIRED: "Sélectionnez le fichier requis pour continuer.",
      INLINE_TEXT_REQUIRED: "Saisissez du texte pour continuer.",
      QUESTIONS_REQUIRED_BEFORE_ANSWERS: "Générez d’abord les questions, puis générez les réponses.",
      PDF_REGION_NO_MATCH: "Aucun contenu correspondant n’a été trouvé dans la zone PDF sélectionnée.",
      OCR_UNAVAILABLE: "La reconnaissance OCR est temporairement indisponible.",
      OCR_LANGUAGE_INVALID: "L’un des paramètres de langue OCR est invalide.",
      SINGLE_INPUT_REQUIRED: "Fournissez une seule source d’entrée puis réessayez.",
      REALTIME_ACTION_UNSUPPORTED: "Cette action en temps réel n’est pas prise en charge.",
      REALTIME_MESSAGE_FAILED: "Le message en temps réel n’a pas pu être traité.",
      SIGNATURE_AREA_TOO_SMALL: "La zone de signature sélectionnée est trop petite. Choisissez une zone plus grande puis réessayez.",
      SIGNING_SERVICE_UNAVAILABLE: "Le service de signature est temporairement indisponible.",
      ATTACHMENT_SECURITY_PENDING: "Cette pièce jointe est temporairement indisponible pendant la vérification de son état de sécurité.",
      COMPLETION_LINK_INVALID: "Ce lien de finalisation est invalide ou n’est plus disponible.",
      COMPLETED_ENVELOPE_LOAD_FAILED: "Impossible de charger l’enveloppe finalisée.",
      COMPLETED_ENVELOPE_DOWNLOAD_FAILED: "Impossible de télécharger le fichier finalisé.",
      SIGNING_LINK_INVALID: "Ce lien de signature est invalide ou n’est plus disponible.",
      SIGNATURE_APPLY_FAILED: "Impossible d’appliquer votre signature.",
      OUTPUT_ARTIFACT_REQUEST_FAILED: "Impossible de récupérer la sortie traitée.",
      BROWSER_FILE_PREPARE_UNAVAILABLE: "Ce navigateur ne peut pas préparer les fichiers pour le partage.",
      OUTPUT_FILE_UNAVAILABLE: "Le fichier de sortie n’est pas disponible.",
      OUTPUT_FILE_EMPTY: "Le fichier de sortie est vide.",
      OUTPUT_SHARE_PREPARE_FAILED: "Impossible de préparer la sortie pour le partage.",
      ORGANIZATION_MEMBERS_LOAD_FAILED: "Impossible de charger les membres de l’organisation pour le partage.",
      SECURE_CONVERSATION_RESOLVE_FAILED: "Impossible de résoudre la conversation ReDOCX sécurisée.",
      SECURE_ATTACHMENT_REFERENCE_MISSING: "La pièce jointe sécurisée a été envoyée, mais aucune référence de message n’a été retournée.",
      DELIVERY_CONFIRMATION_FAILED: "Les livraisons restantes n’ont pas pu être confirmées.",
      SECURE_SHARE_FAILED: "Impossible de partager la sortie de manière sécurisée.",
      BACKEND_RESULT_MISSING: "Le serveur n’a retourné aucun résultat.",
      BACKEND_RESPONSE_INVALID: "Le serveur a retourné une réponse inattendue.",
      DOCUMENT_TYPE_UNSUPPORTED: "Le détecteur de type de document a retourné un type non pris en charge.",
      MEDIA_DURATION_READ_FAILED: "Impossible de lire la durée du média.",
      NO_SPEECH_DETECTED: "Aucune parole intelligible n’a été détectée. Parlez clairement ou essayez un enregistrement contenant une voix audible.",
      BATCH_MEDIA_TYPE_MISMATCH: "Tous les fichiers d’un lot de transcription doivent utiliser le même type de média.",
      TRANSCRIPT_TEXT_MISSING: "Le serveur n’a retourné aucun texte de transcription.",
      TRANSCRIPT_ARTIFACT_MISSING: "La transcription a été générée, mais aucun fichier PDF téléchargeable n’a été retourné.",
      TRANSCRIPT_SUBTITLES_MISSING: "La transcription a été générée, mais la synchronisation des sous-titres ou le média de lecture n’a pas été retourné.",
      RECORDING_DOWNLOAD_FAILED: "Impossible de télécharger l’enregistrement de l’appel.",
      SUBGROUP_CREATE_FAILED: "Impossible de créer la conversation du sous-groupe.",
      MESSAGE_FORWARD_FAILED: "Impossible de transférer le message.",
      COMPRESSION_JOB_TIMEOUT: "La compression dure depuis trop longtemps. Conservez l’identifiant de la tâche et réessayez la demande d’état.",
      COMPRESSION_RESULT_MISSING: "La compression est terminée, mais aucun résultat téléchargeable n’a été produit.",
      COMPRESSION_FAILED: "La compression du PDF a échoué.",
      COMPRESSION_JOB_STATUS_FAILED: "Impossible de lire l’état de la tâche de compression.",
      SIGNATURE_IMAGE_PREPARE_FAILED: "Impossible de préparer l’image de signature.",
      CONVERTED_OUTPUT_UNAVAILABLE: "Le fichier converti n’est pas disponible.",
      CONVERTED_OUTPUT_EMPTY: "Le fichier converti est vide.",
      CONVERTED_SHARE_PREPARE_FAILED: "Impossible de préparer le fichier converti pour le partage.",
      CONVERTED_SECURE_SHARE_FAILED: "Impossible de partager le fichier converti de manière sécurisée.",
      TEAM_REQUEST_FAILED: "La requête d’équipe n’a pas pu être effectuée.",
      PRINT_ARCHIVE_UNSUPPORTED: "Les archives ZIP ne peuvent pas être imprimées directement. Téléchargez et extrayez l’archive, puis imprimez le document requis.",
      PRINT_UNSUPPORTED: "Ce format de sortie ne peut pas être imprimé directement. Téléchargez le fichier et ouvrez-le dans une application compatible avec l’impression.",
      PRINT_FAILED: "Impossible de préparer cette sortie pour l’impression.",
  },
};

export const backendErrorCodeAliases = {
  account_deactivated: "ACCOUNT_DEACTIVATED",
  account_deactivated_pending_deletion: "ACCOUNT_DEACTIVATED",
  account_deactivation_in_progress: "ACCOUNT_DEACTIVATED",
  account_deactivation_pending_retry: "ACCOUNT_OPERATION_FAILED",
  account_delete_failed: "ACCOUNT_OPERATION_FAILED",
  account_deleted: "ACCOUNT_DELETED",
  account_lifecycle_not_configured: "ACCOUNT_OPERATION_FAILED",
  account_lifecycle_unavailable: "ACCOUNT_OPERATION_FAILED",
  account_load_failed: "ACCOUNT_OPERATION_FAILED",
  account_operation_failed: "ACCOUNT_OPERATION_FAILED",
  account_recovery_expired: "ACCOUNT_RECOVERY_EXPIRED",
  account_restore_failed: "ACCOUNT_OPERATION_FAILED",
  account_restore_window_elapsed: "ACCOUNT_RECOVERY_EXPIRED",
  account_update_failed: "ACCOUNT_OPERATION_FAILED",
  active_subscription_required: "BILLING_CONFLICT",
  ai_timeout: "UPSTREAM_TIMEOUT",
  already_on_plan: "BILLING_CONFLICT",
  asr_provider_error: "UPSTREAM_SERVICE_ERROR",
  asr_provider_http_error: "UPSTREAM_SERVICE_ERROR",
  asr_provider_invalid_response: "UPSTREAM_SERVICE_ERROR",
  asr_provider_unavailable: "UPSTREAM_SERVICE_ERROR",
  asr_timeout: "UPSTREAM_TIMEOUT",
  insufficient_speech_detected: "NO_SPEECH_DETECTED",
  attachment_content_mismatch: "ATTACHMENT_INVALID",
  attachment_decryption_key_unavailable: "ATTACHMENT_SECURITY_UNAVAILABLE",
  attachment_download_failed: "TEAM_SERVICE_UNAVAILABLE",
  attachment_encryption_configuration_invalid: "ATTACHMENT_SECURITY_UNAVAILABLE",
  attachment_encryption_not_configured: "ATTACHMENT_SECURITY_UNAVAILABLE",
  attachment_encryption_unsupported: "ATTACHMENT_SECURITY_UNAVAILABLE",
  attachment_image_validator_unavailable: "ATTACHMENT_SECURITY_UNAVAILABLE",
  attachment_integrity_failed: "ATTACHMENT_INTEGRITY_FAILED",
  attachment_integrity_failure: "ATTACHMENT_INTEGRITY_FAILED",
  attachment_invalid: "ATTACHMENT_INVALID",
  attachment_media_validator_unavailable: "ATTACHMENT_SECURITY_UNAVAILABLE",
  attachment_not_forwardable: "REQUEST_CONFLICT",
  attachment_not_found: "ATTACHMENT_NOT_FOUND",
  attachment_not_secured: "ATTACHMENT_SECURITY_UNAVAILABLE",
  attachment_quota_exceeded: "ATTACHMENT_QUOTA_EXCEEDED",
  attachment_request_too_large: "PAYLOAD_TOO_LARGE",
  attachment_required: "ATTACHMENT_INVALID",
  attachment_scanner_database_stale: "ATTACHMENT_SECURITY_UNAVAILABLE",
  attachment_scanner_database_unavailable: "ATTACHMENT_SECURITY_UNAVAILABLE",
  attachment_scanner_unavailable: "ATTACHMENT_SECURITY_UNAVAILABLE",
  attachment_security_schema_not_ready: "ATTACHMENT_SECURITY_UNAVAILABLE",
  attachment_security_storage_unavailable: "ATTACHMENT_SECURITY_UNAVAILABLE",
  attachment_security_unavailable: "ATTACHMENT_SECURITY_UNAVAILABLE",
  attachment_send_failed: "TEAM_SERVICE_UNAVAILABLE",
  attachment_storage_quota_exceeded: "ATTACHMENT_QUOTA_EXCEEDED",
  attachment_too_large: "ATTACHMENT_TOO_LARGE",
  auth0_delete_failed: "AUTH_PROVIDER_UNAVAILABLE",
  auth0_management_forbidden: "AUTH_PROVIDER_UNAVAILABLE",
  auth0_management_invalid_response: "AUTH_PROVIDER_UNAVAILABLE",
  auth0_management_not_configured: "AUTH_PROVIDER_UNAVAILABLE",
  auth0_management_unavailable: "AUTH_PROVIDER_UNAVAILABLE",
  auth0_management_validation_failed: "AUTH_PROVIDER_UNAVAILABLE",
  auth_provider_unavailable: "AUTH_PROVIDER_UNAVAILABLE",
  authorization_required: "AUTHORIZATION_REQUIRED",
  batch_upload_invalid: "BATCH_UPLOAD_INVALID",
  batch_upload_plan_required: "BATCH_UPLOAD_PLAN_REQUIRED",
  billing_access_revoked: "BILLING_CONFLICT",
  billing_conflict: "BILLING_CONFLICT",
  subscription_period_locked: "SUBSCRIPTION_PERIOD_LOCKED",
  billing_handoff_pending: "BILLING_CONFLICT",
  billing_handoff_expired: "BILLING_CONFLICT",
  billing_handoff_authorization_required: "BILLING_CONFLICT",
  billing_handoff_new_owner_required: "ORGANIZATION_PERMISSION_REQUIRED",
  ownership_transfer_renewal_stop_failed: "SUBSCRIPTION_OPERATION_FAILED",
  billing_period_reconciliation_required: "BILLING_CONFLICT",
  billing_operation_conflict: "BILLING_CONFLICT",
  billing_plans_failed: "BILLING_UNAVAILABLE",
  billing_provider_not_configured: "BILLING_UNAVAILABLE",
  billing_schema_not_ready: "BILLING_UNAVAILABLE",
  billing_subscription_reference_missing: "BILLING_CONFLICT",
  billing_unavailable: "BILLING_UNAVAILABLE",
  billing_webhook_failed: "INVALID_WEBHOOK",
  billing_webhook_processing_failed: "INVALID_WEBHOOK",
  call_access_denied: "CALL_ACCESS_DENIED",
  call_already_active: "CALL_STATE_CONFLICT",
  call_already_joined: "CALL_STATE_CONFLICT",
  call_decline_failed: "TEAM_SERVICE_UNAVAILABLE",
  call_end_failed: "TEAM_SERVICE_UNAVAILABLE",
  call_expired: "CALL_STATE_CONFLICT",
  call_host_required: "CALL_ACCESS_DENIED",
  call_invitation_required: "CALL_ACCESS_DENIED",
  call_join_failed: "TEAM_SERVICE_UNAVAILABLE",
  call_leave_failed: "TEAM_SERVICE_UNAVAILABLE",
  call_link_cancelled: "CALL_STATE_CONFLICT",
  call_link_completed: "CALL_STATE_CONFLICT",
  call_link_expired: "CALL_STATE_CONFLICT",
  call_link_has_active_call: "CALL_STATE_CONFLICT",
  call_link_not_found: "CALL_NOT_FOUND",
  call_link_owner_required: "CALL_ACCESS_DENIED",
  call_link_too_early: "CALL_STATE_CONFLICT",
  call_not_active: "CALL_STATE_CONFLICT",
  call_not_found: "CALL_NOT_FOUND",
  call_not_joinable: "CALL_STATE_CONFLICT",
  call_participant_limit_reached: "CALL_STATE_CONFLICT",
  call_participant_not_found: "CALL_NOT_FOUND",
  call_participant_not_joinable: "CALL_ACCESS_DENIED",
  call_participant_required: "CALL_ACCESS_DENIED",
  call_recording_consent_required: "CALL_RECORDING_CONSENT_REQUIRED",
  call_recording_disabled: "CALL_ACCESS_DENIED",
  call_replay_failed: "TEAM_SERVICE_UNAVAILABLE",
  call_start_failed: "TEAM_SERVICE_UNAVAILABLE",
  call_state_conflict: "CALL_STATE_CONFLICT",
  call_telemetry_failed: "TEAM_SERVICE_UNAVAILABLE",
  call_telemetry_not_ready: "TEAM_SERVICE_UNAVAILABLE",
  checkout_provider_failed: "PAYMENT_VERIFICATION_FAILED",
  client_message_id_conflict: "REQUEST_CONFLICT",
  completion_link_invalid: "COMPLETION_LINK_INVALID",
  compression_job_not_found: "RESOURCE_NOT_FOUND",
  conversation_access_denied: "ORGANIZATION_ACCESS_DENIED",
  conversation_create_failed: "TEAM_SERVICE_UNAVAILABLE",
  conversation_not_found: "CONVERSATION_NOT_FOUND",
  conversations_load_failed: "TEAM_SERVICE_UNAVAILABLE",
  dangerous_double_extension: "ATTACHMENT_INVALID",
  downgrade_not_allowed: "BILLING_CONFLICT",
  duplicate_batch_upload: "DUPLICATE_UPLOAD",
  duplicate_upload: "DUPLICATE_UPLOAD",
  email_required: "REQUEST_CONFLICT",
  empty_attachment: "ATTACHMENT_INVALID",
  empty_policy_update: "INVALID_REQUEST",
  external_subscription_cancellation_failed: "SUBSCRIPTION_OPERATION_FAILED",
  external_subscription_cancellation_unsupported: "BILLING_CONFLICT",
  external_subscription_reference_missing: "BILLING_CONFLICT",
  extraction_failed: "EXTRACTION_FAILED",
  feature_not_available: "FEATURE_NOT_AVAILABLE",
  feature_not_configured: "FEATURE_NOT_CONFIGURED",
  file_empty: "FILE_EMPTY",
  file_too_large: "FILE_TOO_LARGE",
  free_account_device_limit_exceeded: "PLAN_LIMIT_EXCEEDED",
  free_device_already_bound: "PLAN_LIMIT_EXCEEDED",
  idempotency_key_reused: "BILLING_CONFLICT",
  input_required: "INPUT_REQUIRED",
  insufficient_scope: "INSUFFICIENT_SCOPE",
  internal_error: "INTERNAL_ERROR",
  invalid_account_delete_request: "INVALID_REQUEST",
  invalid_action: "INVALID_ACTION",
  invalid_attachment: "ATTACHMENT_INVALID",
  invalid_attachment_filename: "ATTACHMENT_INVALID",
  invalid_batch_upload: "BATCH_UPLOAD_INVALID",
  invalid_billing_request: "INVALID_REQUEST",
  invalid_billing_state: "INVALID_REQUEST",
  invalid_billing_webhook: "INVALID_WEBHOOK",
  invalid_checkout_seat_count: "BILLING_CONFLICT",
  invalid_conversation: "INVALID_REQUEST",
  invalid_conversation_members: "INVALID_REQUEST",
  invalid_file_encoding: "INVALID_FILE_ENCODING",
  invalid_forward_request: "INVALID_REQUEST",
  invalid_idempotency_key: "INVALID_REQUEST",
  invalid_image: "ATTACHMENT_INVALID",
  invalid_invitation: "INVALID_REQUEST",
  invalid_json_attachment: "ATTACHMENT_INVALID",
  invalid_media_attachment: "ATTACHMENT_INVALID",
  invalid_member: "INVALID_REQUEST",
  invalid_member_update: "INVALID_REQUEST",
  invalid_message: "INVALID_REQUEST",
  invalid_message_cursor: "INVALID_REQUEST",
  invalid_office_document: "ATTACHMENT_INVALID",
  invalid_organization: "INVALID_REQUEST",
  invalid_organization_name: "INVALID_REQUEST",
  invalid_ownership_transfer: "INVALID_REQUEST",
  invalid_paystack_confirmation: "INVALID_REQUEST",
  invalid_pdf: "ATTACHMENT_INVALID",
  invalid_realtime_frame: "INVALID_REQUEST",
  invalid_request: "INVALID_REQUEST",
  invalid_seat_count: "INVALID_REQUEST",
  invalid_setting: "INVALID_REQUEST",
  invalid_subscription: "INVALID_REQUEST",
  invalid_text_attachment: "ATTACHMENT_INVALID",
  invalid_token: "INVALID_TOKEN",
  invalid_upload_metadata: "INVALID_UPLOAD_METADATA",
  invalid_webhook: "INVALID_WEBHOOK",
  invalid_webhook_signature: "INVALID_WEBHOOK",
  invitation_accept_failed: "TEAM_SERVICE_UNAVAILABLE",
  invitation_acceptance_required: "REQUEST_CONFLICT",
  invitation_cancel_denied: "ORGANIZATION_PERMISSION_REQUIRED",
  invitation_deny_failed: "TEAM_SERVICE_UNAVAILABLE",
  invitation_not_found: "RESOURCE_NOT_FOUND",
  joined_call_participant_required: "CALL_STATE_CONFLICT",
  jwks_invalid: "AUTH_PROVIDER_UNAVAILABLE",
  jwks_unavailable: "AUTH_PROVIDER_UNAVAILABLE",
  last_owner_required: "REQUEST_CONFLICT",
  livekit_not_configured: "TEAM_SERVICE_UNAVAILABLE",
  livekit_sdk_missing: "TEAM_SERVICE_UNAVAILABLE",
  malware_detected: "MALWARE_DETECTED",
  max_accounts_below_active_members: "ORGANIZATION_SEAT_LIMIT_REACHED",
  media_duration_limit_exceeded: "ATTACHMENT_INVALID",
  member_invite_failed: "TEAM_SERVICE_UNAVAILABLE",
  member_not_found: "RESOURCE_NOT_FOUND",
  member_remove_failed: "TEAM_SERVICE_UNAVAILABLE",
  member_update_failed: "TEAM_SERVICE_UNAVAILABLE",
  message_forward_failed: "TEAM_SERVICE_UNAVAILABLE",
  message_not_forwardable: "REQUEST_CONFLICT",
  message_not_found: "RESOURCE_NOT_FOUND",
  message_notifications_load_failed: "TEAM_SERVICE_UNAVAILABLE",
  message_replay_failed: "TEAM_SERVICE_UNAVAILABLE",
  message_send_failed: "TEAM_SERVICE_UNAVAILABLE",
  messages_load_failed: "TEAM_SERVICE_UNAVAILABLE",
  method_not_allowed: "METHOD_NOT_ALLOWED",
  new_owner_must_be_active_member: "REQUEST_CONFLICT",
  not_enough_call_participants: "CALL_STATE_CONFLICT",
  not_enough_organization_members: "CALL_STATE_CONFLICT",
  organization_access_denied: "ORGANIZATION_ACCESS_DENIED",
  organization_admin_required: "ORGANIZATION_PERMISSION_REQUIRED",
  organization_create_failed: "TEAM_SERVICE_UNAVAILABLE",
  organization_leave_failed: "TEAM_SERVICE_UNAVAILABLE",
  organization_load_failed: "TEAM_SERVICE_UNAVAILABLE",
  organization_name_required: "INVALID_REQUEST",
  organization_not_found: "RESOURCE_NOT_FOUND",
  organization_owner_required: "ORGANIZATION_PERMISSION_REQUIRED",
  organization_permission_required: "ORGANIZATION_PERMISSION_REQUIRED",
  organization_seat_limit_reached: "ORGANIZATION_SEAT_LIMIT_REACHED",
  organization_update_failed: "TEAM_SERVICE_UNAVAILABLE",
  organizations_load_failed: "TEAM_SERVICE_UNAVAILABLE",
  owner_invitation_cancel_denied: "ORGANIZATION_PERMISSION_REQUIRED",
  ownership_transfer_failed: "TEAM_SERVICE_UNAVAILABLE",
  ownership_transfer_or_member_removal_required: "REQUEST_CONFLICT",
  paid_concurrency_limit_exceeded: "RATE_LIMIT_EXCEEDED",
  paid_plan_account_limit_exceeded: "PLAN_LIMIT_EXCEEDED",
  paid_subscription_required: "BILLING_CONFLICT",
  participant_limit_exceeds_membership: "INVALID_REQUEST",
  password_protected_file: "PASSWORD_PROTECTED_FILE",
  payload_too_large: "PAYLOAD_TOO_LARGE",
  payment_pending: "PAYMENT_PENDING",
  payment_verification_failed: "PAYMENT_VERIFICATION_FAILED",
  paystack_activation_failed: "PAYMENT_VERIFICATION_FAILED",
  paystack_activation_not_reflected: "PAYMENT_PENDING",
  paystack_checkout_amount_mismatch: "PAYMENT_VERIFICATION_FAILED",
  paystack_checkout_amount_missing: "PAYMENT_VERIFICATION_FAILED",
  paystack_checkout_email_mismatch: "PAYMENT_VERIFICATION_FAILED",
  paystack_checkout_identity_mismatch: "PAYMENT_VERIFICATION_FAILED",
  paystack_checkout_not_found: "PAYMENT_VERIFICATION_FAILED",
  paystack_checkout_plan_mismatch: "PAYMENT_VERIFICATION_FAILED",
  paystack_confirmation_failed: "PAYMENT_PENDING",
  paystack_payment_not_confirmed: "PAYMENT_PENDING",
  paystack_verification_failed: "PAYMENT_VERIFICATION_FAILED",
  pdf_page_limit_exceeded: "ATTACHMENT_INVALID",
  pdf_tool_free_window_exceeded: "RATE_LIMIT_EXCEEDED",
  pdf_tool_guest_trial_used: "AUTHORIZATION_REQUIRED",
  permission_denied: "PERMISSION_DENIED",
  plan_limit_exceeded: "PLAN_LIMIT_EXCEEDED",
  plan_owner_immutable: "ORGANIZATION_PERMISSION_REQUIRED",
  plan_required: "PLAN_REQUIRED",
  presence_load_failed: "TEAM_SERVICE_UNAVAILABLE",
  presence_provider_controlled: "REQUEST_CONFLICT",
  presence_service_unavailable: "TEAM_SERVICE_UNAVAILABLE",
  preview_unavailable: "PREVIEW_UNAVAILABLE",
  print_preview_unavailable: "PREVIEW_UNAVAILABLE",
  processing_failed: "PROCESSING_FAILED",
  processing_output_missing: "PROCESSING_OUTPUT_MISSING",
  processing_resource_limit: "PROCESSING_RESOURCE_LIMIT",
  provider_switch_requires_cancellation: "BILLING_CONFLICT",
  rate_limit_anonymous_device_secret_missing: "RATE_LIMIT_UNAVAILABLE",
  rate_limit_device_secret_missing: "RATE_LIMIT_UNAVAILABLE",
  rate_limit_exceeded: "RATE_LIMIT_EXCEEDED",
  rate_limit_unavailable: "RATE_LIMIT_UNAVAILABLE",
  realtime_identity_change_denied: "PERMISSION_DENIED",
  recording_already_open: "CALL_STATE_CONFLICT",
  recording_consent_required: "CALL_RECORDING_CONSENT_REQUIRED",
  recording_not_found: "RESOURCE_NOT_FOUND",
  recording_not_open: "CALL_STATE_CONFLICT",
  recording_requires_two_participants: "CALL_STATE_CONFLICT",
  request_conflict: "REQUEST_CONFLICT",
  request_too_early: "REQUEST_TOO_EARLY",
  resource_gone: "RESOURCE_GONE",
  resource_not_found: "RESOURCE_NOT_FOUND",
  seat_count_below_active_members: "BILLING_CONFLICT",
  seat_limit_reached: "ORGANIZATION_SEAT_LIMIT_REACHED",
  self_invite_not_allowed: "REQUEST_CONFLICT",
  service_unavailable: "SERVICE_UNAVAILABLE",
  signing_backend_unavailable: "SIGNING_SERVICE_UNAVAILABLE",
  signing_link_expired: "RESOURCE_GONE",
  signing_link_invalid: "SIGNING_LINK_INVALID",
  signing_not_available: "REQUEST_CONFLICT",
  signing_service_unavailable: "SERVICE_UNAVAILABLE",
  source_file_not_found: "SOURCE_FILE_NOT_FOUND",
  subgroup_member_limit_exceeded: "INVALID_REQUEST",
  subgroup_members_required: "INVALID_REQUEST",
  subscription_change_failed: "SUBSCRIPTION_OPERATION_FAILED",
  subscription_load_failed: "SUBSCRIPTION_OPERATION_FAILED",
  subscription_operation_failed: "SUBSCRIPTION_OPERATION_FAILED",
  subscription_resume_failed: "ACCOUNT_OPERATION_FAILED",
  subscription_update_failed: "SUBSCRIPTION_OPERATION_FAILED",
  target_plan_required: "BILLING_CONFLICT",
  team_communications_unavailable: "PLAN_REQUIRED",
  team_service_unavailable: "TEAM_SERVICE_UNAVAILABLE",
  too_many_attachments: "PAYLOAD_TOO_LARGE",
  unknown_attachment_encryption_key: "ATTACHMENT_SECURITY_UNAVAILABLE",
  unsafe_file: "UNSAFE_FILE",
  unsafe_image_dimensions: "ATTACHMENT_INVALID",
  unsafe_image_frames: "ATTACHMENT_INVALID",
  unsafe_media_content: "ATTACHMENT_INVALID",
  unsafe_office_archive: "ATTACHMENT_INVALID",
  unsafe_office_content: "ATTACHMENT_INVALID",
  unsafe_pdf_content: "ATTACHMENT_INVALID",
  unsafe_pdf_structure: "ATTACHMENT_INVALID",
  unsupported_account_realtime_event: "INVALID_ACTION",
  unsupported_attachment_type: "ATTACHMENT_INVALID",
  unsupported_conversion_pair: "UNSUPPORTED_CONVERSION_PAIR",
  unsupported_encrypted_document: "ATTACHMENT_INVALID",
  unsupported_file_type: "UNSUPPORTED_FILE_TYPE",
  unsupported_output_format: "UNSUPPORTED_OUTPUT_FORMAT",
  unsupported_realtime_event: "INVALID_ACTION",
  upgrade_intent_failed: "BILLING_UNAVAILABLE",
  upgrade_not_allowed: "BILLING_CONFLICT",
  upload_persist_failed: "UPLOAD_PERSIST_FAILED",
  upload_security_unavailable: "UPLOAD_SECURITY_UNAVAILABLE",
  upstream_service_error: "UPSTREAM_SERVICE_ERROR",
  upstream_timeout: "UPSTREAM_TIMEOUT",
  user_deleted: "ACCOUNT_DELETED",
  user_not_found: "ACCOUNT_DELETED",
  web_push_not_configured: "TEAM_SERVICE_UNAVAILABLE",
  websocket_origin_denied: "PERMISSION_DENIED",
  websocket_origin_required: "PERMISSION_DENIED",
  workflow_prerequisite_required: "WORKFLOW_PREREQUISITE_REQUIRED",
};

const backendFriendlyMessageTranslationKeys = {
  "Add a file or enter text to continue.": "INPUT_REQUIRED_ADD_FILE_OR_TEXT",
  "Billing is temporarily unavailable. No payment was started. Please try again later.": "BILLING_SCHEMA_UNAVAILABLE",
  "Call presence is managed automatically and can't be changed manually.": "PRESENCE_PROVIDER_MANAGED",
  "Choose the required file to continue.": "INPUT_FILE_REQUIRED",
  "Enter some text to continue.": "INLINE_TEXT_REQUIRED",
  "Generate questions first, then generate answers.": "QUESTIONS_REQUIRED_BEFORE_ANSWERS",
  "No matching content was found in the selected PDF region.": "PDF_REGION_NO_MATCH",
  "OCR is temporarily unavailable.": "OCR_UNAVAILABLE",
  "One of the OCR language settings is invalid.": "OCR_LANGUAGE_INVALID",
  "Provide one input source only and try again.": "SINGLE_INPUT_REQUIRED",
  "That realtime action is not supported.": "REALTIME_ACTION_UNSUPPORTED",
  "The realtime message could not be processed.": "REALTIME_MESSAGE_FAILED",
  "The selected signature area is too small. Choose a larger area and try again.": "SIGNATURE_AREA_TOO_SMALL",
  "The signing service is temporarily unavailable.": "SIGNING_SERVICE_UNAVAILABLE",
  "This attachment is temporarily unavailable while its security state is being verified.": "ATTACHMENT_SECURITY_PENDING",
};

function normalizedErrorLocale(language) {
  return String(language || "en").trim().toLowerCase().startsWith("fr") ? "fr" : "en";
}

function normalizeErrorCodeCandidate(value) {
  const raw = String(value || "").trim();
  if (!raw) return "";
  if (errorTranslations.en[raw]) return raw;
  const upper = raw.replace(/[\s-]+/g, "_").toUpperCase();
  if (errorTranslations.en[upper]) return upper;
  return backendErrorCodeAliases[raw.toLowerCase()] || "";
}

function errorPayloadCandidates(value) {
  if (!value || typeof value !== "object") return [];
  const payload = value.payload && typeof value.payload === "object" ? value.payload : value;
  return [
    value.translationKey,
    value.code,
    payload?.error?.translation_key,
    payload?.error?.translationKey,
    payload?.error?.code,
    payload?.detail?.translation_key,
    payload?.detail?.translationKey,
    payload?.detail?.error,
    payload?.code,
    payload?.errorCode,
  ];
}

function errorMessageCandidates(value) {
  if (!value || typeof value !== "object") return [];
  const payload = value.payload && typeof value.payload === "object" ? value.payload : value;
  return [
    payload?.error?.message,
    payload?.detail?.message,
    typeof payload?.detail === "string" ? payload.detail : "",
    payload?.message,
  ];
}

export function resolveErrorTranslationKey(errorOrCode, fallbackCode = "INTERNAL_ERROR") {
  if (typeof errorOrCode === "string") {
    return normalizeErrorCodeCandidate(errorOrCode) || normalizeErrorCodeCandidate(fallbackCode) || "INTERNAL_ERROR";
  }

  for (const message of errorMessageCandidates(errorOrCode)) {
    const exactKey = backendFriendlyMessageTranslationKeys[String(message || "").trim()];
    if (exactKey) return exactKey;
  }

  for (const candidate of errorPayloadCandidates(errorOrCode)) {
    const key = normalizeErrorCodeCandidate(candidate);
    if (key) return key;
  }

  const messageAsCode = normalizeErrorCodeCandidate(errorOrCode?.message);
  if (messageAsCode) return messageAsCode;

  return normalizeErrorCodeCandidate(fallbackCode) || "INTERNAL_ERROR";
}

export function resolveErrorMessage(errorOrCode, language = "en", fallbackCode = "INTERNAL_ERROR") {
  const locale = normalizedErrorLocale(language);
  const key = resolveErrorTranslationKey(errorOrCode, fallbackCode);
  return (
    errorTranslations[locale]?.[key] ||
    errorTranslations.en[key] ||
    errorTranslations[locale]?.INTERNAL_ERROR ||
    errorTranslations.en.INTERNAL_ERROR
  );
}


// Residual page-level UI copy that is shared by the page implementations but does
// not belong to their historical page translation objects. Keeping this in one
// module prevents page-local language dictionaries and English-only UI fallbacks.
export const pageRuntimeTranslations = {
  billing: {
    en: {
      paymentVerified: "Payment verified. Your plan is active.",
      subscriptionUpdated: "Subscription updated.",
    },
    fr: {
      paymentVerified: "Paiement vérifié. Votre forfait est actif.",
      subscriptionUpdated: "Abonnement mis à jour.",
    },
  },
  compressPdf: {
    en: {
      queued: "Compression job queued.",
      completed: "Compression completed.",
      jobId: "Job ID:",
      compressedPdfTitle: "Compressed PDF",
      batchResultsTitle: "Batch PDF compression results",
      batchOutputTitle: "Batch PDF compression output",
      compressionLevelHelp: {
        small_file: "Maximum compression · approximately 96 DPI for images",
        balanced: "Balanced quality and size · approximately 150 DPI for images",
        high_quality: "Higher visual quality · approximately 300 DPI for images",
      },
      statuses: {
        queued: "Queued",
        processing: "Processing",
        completed: "Completed",
        failed: "Failed",
        cancelled: "Cancelled",
      },
      batchProgress: "{completed}/{total} complete · {status}",
      batchJobsComplete: "{completed}/{total} compression jobs complete",
    },
    fr: {
      queued: "Tâche de compression mise en file d’attente.",
      completed: "Compression terminée.",
      jobId: "Identifiant de la tâche :",
      compressedPdfTitle: "PDF compressé",
      batchResultsTitle: "Résultats de compression PDF par lot",
      batchOutputTitle: "Sortie de compression PDF par lot",
      compressionLevelHelp: {
        small_file: "Compression maximale · environ 96 PPP pour les images",
        balanced: "Équilibre entre qualité et taille · environ 150 PPP pour les images",
        high_quality: "Qualité visuelle supérieure · environ 300 PPP pour les images",
      },
      statuses: {
        queued: "En attente",
        processing: "Traitement en cours",
        completed: "Terminé",
        failed: "Échec",
        cancelled: "Annulé",
      },
      batchProgress: "{completed}/{total} terminé(s) · {status}",
      batchJobsComplete: "{completed}/{total} tâches de compression terminées",
    },
  },
  compliance: {
    en: { reportTitle: "Compliance report" },
    fr: { reportTitle: "Rapport de conformité" },
  },
  editPdf: {
    en: {
      pdfLimit: "PDF · 100 MB maximum",
      pagesHash: "page(s) · SHA-256",
      outputTitle: "Edited PDF",
    },
    fr: {
      pdfLimit: "PDF · 100 Mo maximum",
      pagesHash: "page(s) · SHA-256",
      outputTitle: "PDF modifié",
    },
  },
  splitPdf: {
    en: { outputTitle: "Split PDF output" },
    fr: { outputTitle: "Sortie du PDF fractionné" },
  },
  generateQuestions: {
    en: {
      batchQuestionsTitle: "Batch generated questions",
      batchAnswersTitle: "Batch generated answers",
      batchReady: "Batch questions ready",
      questionsParsedAcross: "questions parsed across",
      parsedSummaryOne: "{questions} questions parsed across {files} file.",
      parsedSummaryMany: "{questions} questions parsed across {files} files.",
    },
    fr: {
      batchQuestionsTitle: "Questions générées par lot",
      batchAnswersTitle: "Réponses générées par lot",
      batchReady: "Questions du lot prêtes",
      questionsParsedAcross: "questions analysées sur",
      parsedSummaryOne: "{questions} questions analysées dans {files} fichier.",
      parsedSummaryMany: "{questions} questions analysées dans {files} fichiers.",
    },
  },
  dataMask: {
    en: { outputTitle: "Masked output", previewTitle: "Processed preview" },
    fr: { outputTitle: "Sortie masquée", previewTitle: "Aperçu traité" },
  },
  redact: {
    en: { outputTitle: "Redacted output", previewTitle: "Processed preview" },
    fr: { outputTitle: "Sortie expurgée", previewTitle: "Aperçu traité" },
  },
  teamSettings: {
    en: {
      teamMember: "Team member",
      noEmail: "No email available",
      requestFailed: "Request failed",
      ownershipTransferred: "Plan ownership was transferred.",
      newOwner: "New owner",
      transferring: "Transferring...",
      transfer: "Transfer",
    },
    fr: {
      teamMember: "Membre de l’équipe",
      noEmail: "Aucune adresse e-mail disponible",
      requestFailed: "La requête a échoué",
      ownershipTransferred: "La propriété du forfait a été transférée.",
      newOwner: "Nouveau propriétaire",
      transferring: "Transfert en cours...",
      transfer: "Transférer",
    },
  },
  summarize: {
    en: {
      chooseFile: "Please choose a file.",
      enterText: "Please enter text to summarize.",
      batchResultsTitle: "Batch summarization results",
      batchOutputTitle: "Batch summarization output",
      downloadFile: "Download summarized file",
      outputTitle: "Summarized output",
    },
    fr: {
      chooseFile: "Veuillez choisir un fichier.",
      enterText: "Veuillez saisir le texte à résumer.",
      batchResultsTitle: "Résultats de résumé par lot",
      batchOutputTitle: "Sortie de résumé par lot",
      downloadFile: "Télécharger le fichier résumé",
      outputTitle: "Sortie résumée",
    },
  },
  projectsTeam: {
    en: {
      requestFailed: "Request failed",
      noEmail: "No email available",
      teamMember: "Team member",
      attachment: "Attachment",
      moreAttachments: "and {count} more attachments",
      audioDescription: "OGG audio",
      minuteUnit: "min ·",
      teamName: "Équipe",
      teamName: "Team",
    },
    fr: {
      requestFailed: "La requête a échoué",
      noEmail: "Aucune adresse e-mail disponible",
      teamMember: "Membre de l’équipe",
      attachment: "Pièce jointe",
      moreAttachments: "et {count} autres pièces jointes",
      audioDescription: "Audio OGG",
      minuteUnit: "min ·",
    },
  },
  transcribe: {
    en: {
      batchResultsTitle: "Batch speech-to-text results",
      batchOutputTitle: "Batch speech-to-text output",
      downloadPdf: "Download PDF transcript",
      outputTitle: "Transcript output",
    },
    fr: {
      batchResultsTitle: "Résultats de transcription par lot",
      batchOutputTitle: "Sortie de transcription par lot",
      downloadPdf: "Télécharger la transcription PDF",
      outputTitle: "Sortie de transcription",
    },
  },
  translate: {
    en: {
      batchResultsTitle: "Batch translation results",
      batchOutputTitle: "Batch translation output",
      downloadFile: "Download translated file",
      outputTitle: "Translated output",
    },
    fr: {
      batchResultsTitle: "Résultats de traduction par lot",
      batchOutputTitle: "Sortie de traduction par lot",
      downloadFile: "Télécharger le fichier traduit",
      outputTitle: "Sortie traduite",
    },
  },
  convert: {
    en: {
      excelWorkbook: "Excel workbook",
      htmlDocument: "HTML document",
      powerpointPresentation: "PowerPoint presentation",
      pdfA: "PDF/A (.pdf)",
      downloadOutput: "Download output",
      convertedFile: "Converted file",
      downloadReady: "Download ready",
      convertedReady: "Your converted file is ready to download.",
      missingDownload: "Conversion finished, but the backend did not return a download URL.",
      conversionResult: "Conversion result",
      batchResultsTitle: "Batch conversion results",
      batchOutputTitle: "Batch conversion output",
      outputTitle: "Converted output",
      fileShared: "File shared successfully.",
    },
    fr: {
      excelWorkbook: "Classeur Excel",
      htmlDocument: "Document HTML",
      powerpointPresentation: "Présentation PowerPoint",
      pdfA: "PDF/A (.pdf)",
      downloadOutput: "Télécharger la sortie",
      convertedFile: "Fichier converti",
      downloadReady: "Téléchargement prêt",
      convertedReady: "Votre fichier converti est prêt à être téléchargé.",
      missingDownload: "La conversion est terminée, mais le serveur n’a retourné aucune URL de téléchargement.",
      conversionResult: "Résultat de conversion",
      batchResultsTitle: "Résultats de conversion par lot",
      batchOutputTitle: "Sortie de conversion par lot",
      outputTitle: "Sortie convertie",
      fileShared: "Fichier partagé avec succès.",
    },
  },
  eSignature: {
    en: { fieldFallback: "{type} {number}" },
    fr: { fieldFallback: "{type} {number}" },
  },
  explain: {
    en: {
      batchResultsTitle: "Batch explanation results",
      batchOutputTitle: "Batch explanation output",
      fileLabel: "File:",
      formatLabel: "Format:",
      sizeLabel: "Size:",
      downloadFile: "Download explained file",
      outputTitle: "Explained output",
    },
    fr: {
      batchResultsTitle: "Résultats d’explication par lot",
      batchOutputTitle: "Sortie d’explication par lot",
      fileLabel: "Fichier :",
      formatLabel: "Format :",
      sizeLabel: "Taille :",
      downloadFile: "Télécharger le fichier expliqué",
      outputTitle: "Sortie expliquée",
    },
  },
  structuredExtraction: {
    en: {
      maxFiles: "Structured extraction accepts a maximum of {count} files.",
      documentLabel: "Document {number}",
      outputTitle: "Structured extraction output",
    },
    fr: {
      maxFiles: "L’extraction structurée accepte au maximum {count} fichiers.",
      documentLabel: "Document {number}",
      outputTitle: "Sortie de l’extraction structurée",
    },
  },
  grammar: {
    en: {
      chooseFile: "Please choose a file.",
      enterText: "Please enter text to correct.",
      batchResultsTitle: "Batch grammar correction results",
      batchOutputTitle: "Batch grammar correction output",
      fileLabel: "File:",
      formatLabel: "Format:",
      sizeLabel: "Size:",
      outputTitle: "Grammar-corrected output",
    },
    fr: {
      chooseFile: "Veuillez choisir un fichier.",
      enterText: "Veuillez saisir le texte à corriger.",
      batchResultsTitle: "Résultats de correction grammaticale par lot",
      batchOutputTitle: "Sortie de correction grammaticale par lot",
      fileLabel: "Fichier :",
      formatLabel: "Format :",
      sizeLabel: "Taille :",
      outputTitle: "Sortie corrigée grammaticalement",
    },
  },
  combinePdf: {
    en: { outputTitle: "Combined PDF" },
    fr: { outputTitle: "PDF combiné" },
  },
  home: {
    en: {
      openSidebar: "Open sidebar",
      closeSidebar: "Close sidebar",
      openAccountMenu: "Open account menu",
    },
    fr: {
      openSidebar: "Ouvrir la barre latérale",
      closeSidebar: "Fermer la barre latérale",
      openAccountMenu: "Ouvrir le menu du compte",
    },
  },
};

export function getPageRuntimeCopy(section, language = "en") {
  const locale = language === "fr" ? "fr" : "en";
  return pageRuntimeTranslations?.[section]?.[locale] || pageRuntimeTranslations?.[section]?.en || {};
}
