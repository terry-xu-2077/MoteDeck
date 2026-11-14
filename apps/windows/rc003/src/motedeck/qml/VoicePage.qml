import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import MoteDeckSettings 1.0

// 语音页：只管本机语音识别服务（CapsWriter-Offline）——位置、模型、端口、启停。
// 遥控器语音键不需要在这里配置：在「按键」页把话筒键指到「CapsWriter」语音键即可，
// 音频由 MoteDeck 经蓝牙直接送进这里的服务识别，不走虚拟声卡。
Item {
    id: root
    signal openDeviceRequested()
    signal openButtonsRequested()
    property var tokens
    property var backTabTarget: null
    property var tabTarget: null
    readonly property var firstFocusItem: toggleServiceButton
    readonly property var lastFocusItem: openLogsButton

    // main.qml 在切页/关窗前会调用每个页面的这个钩子；本页没有需要收尾的录入。
    function settleInputUiAfterStop() {}

    // CapsWriter 直连语音键（录音增益存在这条键上）。
    readonly property var capsWriterKey: {
        const rows = SettingsController.voiceKeyConfigs
        for (let i = 0; i < rows.length; ++i)
            if (String(rows[i].plugin) === "capswriter")
                return rows[i]
        return null
    }

    Component.onCompleted: {
        CapsWriterController.refresh()
        if (visible)
            CapsWriterController.startLive()
    }
    onVisibleChanged: {
        if (visible) {
            CapsWriterController.refresh()
            CapsWriterController.startLive()
        } else {
            CapsWriterController.stopLive()
        }
    }

    FolderDialog {
        id: folderDialog
        title: qsTr("选择 CapsWriter-Offline 目录")
        onAccepted: CapsWriterController.setInstallRoot(selectedFolder.toString())
    }

    ScrollView {
        id: voiceScroll
        objectName: "voiceScroll"
        anchors.fill: parent
        anchors.bottomMargin: 48
        clip: true
        contentWidth: availableWidth
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        ScrollBar.vertical.policy: ScrollBar.AsNeeded

        ColumnLayout {
            id: voicePageContent
            objectName: "voicePageContent"
            width: voiceScroll.availableWidth
            spacing: 16

            Item {
                id: voicePageHeader
                objectName: "voicePageHeader"
                Layout.fillWidth: true
                Layout.preferredHeight: 60

                RowLayout {
                    anchors.left: parent.left
                    anchors.right: parent.right
                    height: 40
                    spacing: 12

                    Label {
                        objectName: "voicePageTitle"
                        text: qsTr("语音")
                        font.family: tokens.fontFamilyDisplay
                        font.pixelSize: tokens.fontSizePageTitle
                        font.weight: Font.DemiBold
                        color: tokens.textPrimary
                        Layout.alignment: Qt.AlignBaseline
                    }
                    Label {
                        objectName: "voicePageSubtitle"
                        text: qsTr("本地语音识别服务")
                        font.family: tokens.fontFamily
                        font.pixelSize: tokens.fontSizeBody
                        color: tokens.textTertiary
                        Layout.alignment: Qt.AlignBaseline
                        Layout.fillWidth: true
                    }
                }
            }

            SectionFrame {
                objectName: "voiceServiceSection"
                tokens: root.tokens
                Layout.fillWidth: true
                titleText: qsTr("语音服务")

                SettingRow {
                    tokens: root.tokens
                    Layout.fillWidth: true
                    titleText: qsTr("服务状态")
                    descriptionText: !CapsWriterController.found
                        ? qsTr("未找到 CapsWriter，请在下方指定目录")
                        : (CapsWriterController.running
                            ? qsTr("运行中，端口 %1").arg(CapsWriterController.port)
                            : qsTr("未运行"))
                    CompactButton {
                        id: toggleServiceButton
                        objectName: "capsWriterToggleButton"
                        tokens: root.tokens
                        visible: CapsWriterController.found
                        highlighted: !CapsWriterController.running
                        enabled: !CapsWriterController.busy
                        text: CapsWriterController.busy ? qsTr("处理中…")
                            : (CapsWriterController.running ? qsTr("停止") : qsTr("启动"))
                        onClicked: CapsWriterController.running
                            ? CapsWriterController.stop() : CapsWriterController.start()
                    }
                    CompactButton {
                        objectName: "capsWriterRestartButton"
                        tokens: root.tokens
                        visible: CapsWriterController.found && CapsWriterController.running
                        enabled: !CapsWriterController.busy
                        flat: true
                        text: qsTr("重启")
                        onClicked: CapsWriterController.restart()
                    }
                }

                SettingRow {
                    tokens: root.tokens
                    Layout.fillWidth: true
                    titleText: qsTr("服务位置")
                    descriptionText: CapsWriterController.installRoot.length > 0
                        ? CapsWriterController.installRoot : qsTr("未设置")
                    CompactButton {
                        tokens: root.tokens
                        flat: true
                        enabled: CapsWriterController.found
                        text: qsTr("打开")
                        onClicked: CapsWriterController.openFolder()
                    }
                    CompactButton {
                        objectName: "capsWriterBrowseButton"
                        tokens: root.tokens
                        text: qsTr("选择目录…")
                        onClicked: folderDialog.open()
                    }
                }

                SettingRow {
                    tokens: root.tokens
                    Layout.fillWidth: true
                    titleText: qsTr("识别模型")
                    descriptionText: CapsWriterController.modelInstalled
                        ? qsTr("切换后如服务正在运行会自动重启")
                        : qsTr("当前模型文件缺失，请先放进 models 目录")
                    SelectionComboBox {
                        objectName: "capsWriterModelCombo"
                        tokens: root.tokens
                        enabled: CapsWriterController.found && !CapsWriterController.busy
                        model: CapsWriterController.models
                        textRole: "label"
                        currentIndex: {
                            const list = CapsWriterController.models
                            for (let i = 0; i < list.length; ++i)
                                if (list[i].key === CapsWriterController.modelType)
                                    return i
                            return -1
                        }
                        onActivated: (index) => CapsWriterController.setModel(
                            CapsWriterController.models[index].key)
                    }
                }

                SettingRow {
                    tokens: root.tokens
                    Layout.fillWidth: true
                    titleText: qsTr("Qwen3-ASR 模型")
                    descriptionText: CapsWriterController.downloading
                        ? CapsWriterController.downloadText
                        : (CapsWriterController.installedVariant.length > 0
                            ? qsTr("已安装") : qsTr("未安装，选择版本后点击下载"))
                    SelectionComboBox {
                        id: variantCombo
                        objectName: "capsWriterVariantCombo"
                        tokens: root.tokens
                        enabled: CapsWriterController.found && !CapsWriterController.busy
                        model: CapsWriterController.modelVariants
                        textRole: "label"
                        currentIndex: {
                            const list = CapsWriterController.modelVariants
                            for (let i = 0; i < list.length; ++i)
                                if (list[i].key === CapsWriterController.installedVariant)
                                    return i
                            return 0
                        }
                    }
                    CompactButton {
                        objectName: "capsWriterDownloadButton"
                        tokens: root.tokens
                        visible: !CapsWriterController.downloading
                        highlighted: CapsWriterController.installedVariant.length === 0
                        enabled: CapsWriterController.found && !CapsWriterController.busy
                        text: CapsWriterController.installedVariant === variantCombo.model[variantCombo.currentIndex].key
                            ? qsTr("重新下载") : qsTr("下载")
                        onClicked: CapsWriterController.downloadModel(
                            CapsWriterController.modelVariants[variantCombo.currentIndex].key)
                    }
                    CompactButton {
                        objectName: "capsWriterCancelDownloadButton"
                        tokens: root.tokens
                        visible: CapsWriterController.downloading
                        flat: true
                        text: qsTr("取消")
                        onClicked: CapsWriterController.cancelDownload()
                    }
                }

                Item {
                    objectName: "capsWriterDownloadProgress"
                    Layout.fillWidth: true
                    Layout.preferredHeight: visible ? 14 : 0
                    visible: CapsWriterController.downloading
                    Rectangle {
                        anchors.verticalCenter: parent.verticalCenter
                        width: parent.width
                        height: 4
                        radius: 2
                        color: root.tokens.line
                        Rectangle {
                            width: parent.width * CapsWriterController.downloadProgress
                            height: parent.height
                            radius: 2
                            color: root.tokens.accent
                        }
                    }
                }

                SettingRow {
                    tokens: root.tokens
                    Layout.fillWidth: true
                    showDivider: false
                    titleText: qsTr("服务端口")
                    descriptionText: qsTr("修改后会同步到服务和客户端")
                    CompactTextField {
                        id: portField
                        objectName: "capsWriterPortField"
                        tokens: root.tokens
                        Layout.preferredWidth: 80
                        enabled: CapsWriterController.found && !CapsWriterController.busy
                        inputMethodHints: Qt.ImhDigitsOnly
                        validator: IntValidator { bottom: 1; top: 65535 }
                        text: String(CapsWriterController.port)
                        onEditingFinished: CapsWriterController.setPort(text)
                    }
                }
            }

            SectionFrame {
                objectName: "voiceBehaviorSection"
                tokens: root.tokens
                Layout.fillWidth: true
                titleText: qsTr("启动与键盘")

                SettingRow {
                    tokens: root.tokens
                    Layout.fillWidth: true
                    titleText: qsTr("随 MoteDeck 启动")
                    descriptionText: qsTr("打开 MoteDeck 时自动启动语音服务")
                    CompactSwitch {
                        objectName: "capsWriterAutoStartSwitch"
                        tokens: root.tokens
                        checked: CapsWriterController.autoStart
                        onToggled: CapsWriterController.setAutoStart(checked)
                    }
                }

                SettingRow {
                    tokens: root.tokens
                    Layout.fillWidth: true
                    showDivider: false
                    titleText: qsTr("同时启动键盘听写")
                    descriptionText: qsTr("CapsWriter 客户端：按住键盘 Caps Lock 说话；遥控器不依赖它")
                    CompactSwitch {
                        objectName: "capsWriterStartClientSwitch"
                        tokens: root.tokens
                        checked: CapsWriterController.startClient
                        onToggled: CapsWriterController.setStartClient(checked)
                    }
                }
            }

            SectionFrame {
                objectName: "voiceGainSection"
                tokens: root.tokens
                Layout.fillWidth: true
                titleText: qsTr("录音")

                SettingRow {
                    tokens: root.tokens
                    Layout.fillWidth: true
                    rowHeight: 64
                    titleText: qsTr("录音增益")
                    descriptionText: root.capsWriterKey === null
                        ? qsTr("还没有 CapsWriter 语音键")
                        : qsTr("−3 ~ +3 共七档，每档 6 dB；开启自动后按峰值拉齐")

                    UiLabel {
                        tokens: root.tokens
                        kind: noteKind
                        text: qsTr("−3")
                    }
                    Slider {
                        id: gainSlider
                        objectName: "voiceGainSlider"
                        Layout.preferredWidth: 160
                        from: -3
                        to: 3
                        stepSize: 1
                        snapMode: Slider.SnapAlways
                        enabled: root.capsWriterKey !== null && !gainAutoSwitch.checked
                        value: root.capsWriterKey ? Number(root.capsWriterKey.gainSteps || 0) : 0
                        onMoved: SettingsController.setVoiceKeyConfigGain(
                            root.capsWriterKey.id, Math.round(value), false)

                        background: Rectangle {
                            x: gainSlider.leftPadding
                            y: gainSlider.topPadding + gainSlider.availableHeight / 2 - height / 2
                            width: gainSlider.availableWidth
                            height: 4
                            radius: 2
                            color: root.tokens.line
                            Rectangle {
                                width: gainSlider.visualPosition * parent.width
                                height: parent.height
                                radius: 2
                                color: root.tokens.accent
                            }
                        }
                        handle: Rectangle {
                            x: gainSlider.leftPadding
                                + gainSlider.visualPosition * (gainSlider.availableWidth - width)
                            y: gainSlider.topPadding + gainSlider.availableHeight / 2 - height / 2
                            width: 16
                            height: 16
                            radius: 8
                            color: root.tokens.accent
                            border.color: root.tokens.borderStrong
                        }
                    }
                    UiLabel {
                        tokens: root.tokens
                        kind: noteKind
                        text: qsTr("+3")
                    }
                    UiLabel {
                        objectName: "voiceGainValue"
                        tokens: root.tokens
                        Layout.preferredWidth: 28
                        horizontalAlignment: Text.AlignHCenter
                        text: {
                            const v = Math.round(gainSlider.value)
                            return v === 0 ? "0" : (v > 0 ? "+" : "−") + Math.abs(v)
                        }
                    }
                    UiLabel {
                        tokens: root.tokens
                        kind: noteKind
                        text: qsTr("自动")
                    }
                    CompactSwitch {
                        id: gainAutoSwitch
                        objectName: "voiceGainAutoSwitch"
                        tokens: root.tokens
                        enabled: root.capsWriterKey !== null
                        checked: root.capsWriterKey ? root.capsWriterKey.gainAuto === true : false
                        onToggled: SettingsController.setVoiceKeyConfigGain(
                            root.capsWriterKey.id, Math.round(gainSlider.value), checked)
                    }
                }

                SettingRow {
                    tokens: root.tokens
                    Layout.fillWidth: true
                    titleText: qsTr("识别测试")
                    descriptionText: CapsWriterController.testMode
                        ? qsTr("测试中：按住遥控器话筒键说话，结果只显示在这里，不会上屏")
                        : qsTr("开启后按住遥控器话筒键说话，只在本页显示电平和识别结果")
                    CompactSwitch {
                        objectName: "capsWriterTestModeSwitch"
                        tokens: root.tokens
                        checked: CapsWriterController.testMode
                        onToggled: CapsWriterController.setTestMode(checked)
                    }
                }

                SettingRow {
                    tokens: root.tokens
                    Layout.fillWidth: true
                    visible: CapsWriterController.testMode
                    rowHeight: 64
                    titleText: CapsWriterController.livePhase === "listening" ? qsTr("正在听…")
                        : CapsWriterController.livePhase === "recognizing" ? qsTr("识别中…")
                        : qsTr("最近一次")
                    descriptionText: CapsWriterController.liveError.length > 0
                        ? CapsWriterController.liveError
                        : (CapsWriterController.liveText.length > 0
                            ? CapsWriterController.liveText : qsTr("还没有识别结果"))
                }

                SettingRow {
                    tokens: root.tokens
                    Layout.fillWidth: true
                    showDivider: false
                    titleText: qsTr("连通测试")
                    descriptionText: qsTr("向语音服务发送一段测试音频，确认服务可用")
                    CompactButton {
                        objectName: "capsWriterTestButton"
                        tokens: root.tokens
                        enabled: CapsWriterController.found && CapsWriterController.running
                            && !CapsWriterController.busy
                        text: CapsWriterController.busy ? qsTr("测试中…") : qsTr("测试")
                        onClicked: CapsWriterController.testService()
                    }
                }
            }

            SectionFrame {
                objectName: "voiceRemoteSection"
                tokens: root.tokens
                Layout.fillWidth: true
                titleText: qsTr("遥控器语音")

                SettingRow {
                    tokens: root.tokens
                    Layout.fillWidth: true
                    showDivider: false
                    titleText: qsTr("遥控器话筒键")
                    descriptionText: qsTr("在「按键」页把话筒键设为「CapsWriter」；按住说话，松手上屏")
                    CompactButton {
                        objectName: "openButtonsPageButton"
                        tokens: root.tokens
                        flat: true
                        text: qsTr("前往按键")
                        onClicked: root.openButtonsRequested()
                    }
                    CompactButton {
                        id: openLogsButton
                        objectName: "capsWriterOpenLogsButton"
                        tokens: root.tokens
                        flat: true
                        enabled: CapsWriterController.found
                        text: qsTr("服务日志")
                        onClicked: CapsWriterController.openLogs()
                    }
                }
            }

            UiLabel {
                tokens: root.tokens
                Layout.fillWidth: true
                kind: noteKind
                visible: CapsWriterController.message.length > 0
                wrapMode: Text.WordWrap
                text: CapsWriterController.message
            }

            Item { Layout.preferredHeight: 24 }
        }
    }

    // ── 底部电平纹波：显示遥控器话筒实时电平（由常驻进程经 voice-live.json 传来）──
    WaveRipple {
        objectName: "voiceLevelRipple"
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.bottomMargin: -root.tokens.pageBottomPadding
        height: 52
        tokens: root.tokens
        level: CapsWriterController.liveLevel
    }
}
