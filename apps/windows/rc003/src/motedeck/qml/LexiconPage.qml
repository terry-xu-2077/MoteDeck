import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import MoteDeckSettings 1.0

// 热词页：按「场景」管理纠错词表——场景里的词说得越多，场景越热、纠错越先启用，
// 停下来会自动冷却。别名属于场景，只在该场景生效时才替换，不影响别的话题。
// 引擎在常驻进程里跑（voice_lexicon），这里只改设置文件、读状态来显示。
//
// 版面：整页高度固定（填满窗口），长度只取决于窗口，不取决于场景/别名/记录有多少——
//   顶部一条设置（三列等宽）；
//   下面左右两个面板，边界上下对齐、高度一致：
//     左：场景列表（多了就在列表里滚动）；
//     右：选中场景的「别名 / 词表」和「最近识别」，用三个页签切换。
Item {
    id: root
    signal openDeviceRequested()
    signal openButtonsRequested()
    property var tokens
    property var backTabTarget: null
    property var tabTarget: null
    readonly property var firstFocusItem: null
    readonly property var lastFocusItem: null

    // 选中的场景：默认第一个；被删掉后自动回退。
    property string selectedSceneId: ""
    readonly property var scenes: CapsWriterController.lexicon.scenes
    readonly property var selectedScene: {
        for (let i = 0; i < scenes.length; ++i)
            if (scenes[i].id === selectedSceneId)
                return scenes[i]
        return scenes.length > 0 ? scenes[0] : null
    }
    readonly property string selectedKey: selectedScene ? selectedScene.id : ""
    // 别名表格的行：手动添加的 + 自动学到的
    readonly property var aliasRows: {
        const scene = selectedScene
        if (!scene)
            return []
        const rows = []
        for (let i = 0; i < scene.aliases.length; ++i)
            rows.push({ wrong: scene.aliases[i].wrong, right: scene.aliases[i].right, learned: false })
        for (let j = 0; j < scene.learned.length; ++j)
            rows.push({ wrong: scene.learned[j].wrong, right: scene.learned[j].right, learned: true })
        return rows
    }
    // 聊天记录：旧的在上、新的在下
    readonly property var chatMessages: CapsWriterController.lexicon.history.slice(0, 40).reverse()
    readonly property int termCount: selectedScene && selectedScene.termsText.length > 0
        ? selectedScene.termsText.split("，").length : 0

    // 右侧页签：0 别名 / 1 词表 / 2 最近识别
    property int detailTab: 0
    // 表格列宽：表头和每一行共用，保证上下对齐
    readonly property int sceneModeWidth: 96
    readonly property int sceneActionWidth: 44
    readonly property int aliasWrongWidth: 130
    readonly property int aliasArrowWidth: 20
    readonly property int aliasSourceWidth: 64
    readonly property int aliasActionWidth: 52
    readonly property int panelPadding: 16

    // main.qml 在切页/关窗前会调用每个页面的这个钩子；本页没有需要收尾的录入。
    function settleInputUiAfterStop() {}

    function openAliasDraft(wrong) {
        detailTab = 0
        aliasWrongField.text = wrong
        Qt.callLater(function() { aliasRightField.forceActiveFocus() })
    }

    Component.onCompleted: {
        if (visible)
            CapsWriterController.lexicon.start()
    }
    onVisibleChanged: {
        if (visible)
            CapsWriterController.lexicon.start()
        else
            CapsWriterController.lexicon.stop()
    }

    // 表格里的表头单元格
    component HeaderCell: Label {
        font.family: root.tokens.fontFamily
        font.pixelSize: 12
        font.weight: Font.DemiBold
        color: root.tokens.textTertiary
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
    // 表格里的内容单元格
    component BodyCell: Label {
        font.family: root.tokens.fontFamily
        font.pixelSize: 13
        color: root.tokens.textPrimary
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
    // 面板：标题 + 细线 + 圆角卡片；卡片可以按父布局拉满高度
    component Panel: ColumnLayout {
        id: panel
        property string titleText: ""
        // fill=true：卡片拉满父布局分给它的高度（内容在里面滚动）；false：按内容定高
        property bool fill: true
        default property alias contentData: panelContent.data
        spacing: 8

        RowLayout {
            Layout.fillWidth: true
            Layout.preferredHeight: 20
            spacing: 8
            Label {
                text: panel.titleText
                color: root.tokens.textTertiary
                font.family: root.tokens.fontFamily
                font.pixelSize: 12
                font.weight: Font.DemiBold
                verticalAlignment: Text.AlignVCenter
                elide: Text.ElideRight
            }
            Rectangle {
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignVCenter
                Layout.preferredHeight: 1
                color: root.tokens.line
            }
        }
        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: panel.fill
            Layout.preferredHeight: panel.fill ? -1 : panelContent.implicitHeight + 24
            radius: root.tokens.cornerRadiusCard
            color: root.tokens.surface
            border.width: root.tokens.hairlineWidth
            border.color: root.tokens.line

            ColumnLayout {
                id: panelContent
                anchors.fill: parent
                anchors.leftMargin: root.panelPadding
                anchors.rightMargin: root.panelPadding
                anchors.topMargin: 12
                anchors.bottomMargin: 12
                spacing: 0
            }
        }
    }
    // 设置条里的一列：标题 + 开关在同一行垂直居中，说明在下面
    component SettingColumn: ColumnLayout {
        id: column
        property string titleText: ""
        property string descriptionText: ""
        property alias switchData: switchHolder.data
        property alias belowData: belowHolder.data
        Layout.fillWidth: true
        Layout.preferredWidth: 1
        Layout.alignment: Qt.AlignTop
        spacing: 4

        RowLayout {
            Layout.fillWidth: true
            spacing: 12
            Label {
                Layout.fillWidth: true
                text: column.titleText
                font.family: root.tokens.fontFamily
                font.pixelSize: 13
                font.weight: Font.DemiBold
                color: root.tokens.textPrimary
                elide: Text.ElideRight
            }
            RowLayout {
                id: switchHolder
                Layout.alignment: Qt.AlignVCenter
            }
        }
        Label {
            Layout.fillWidth: true
            text: column.descriptionText
            wrapMode: Text.Wrap
            maximumLineCount: 3
            elide: Text.ElideRight
            font.family: root.tokens.fontFamily
            font.pixelSize: 12
            lineHeight: 16
            lineHeightMode: Text.FixedHeight
            color: root.tokens.textTertiary
        }
        RowLayout {
            id: belowHolder
            Layout.topMargin: 4
            visible: children.length > 0
        }
    }

    ScrollView {
        id: lexiconScroll
        objectName: "lexiconScroll"
        anchors.fill: parent
        anchors.bottomMargin: 24
        clip: true
        contentWidth: availableWidth
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        ScrollBar.vertical.policy: ScrollBar.AsNeeded

        ColumnLayout {
            id: pageContent
            objectName: "lexiconPageContent"
            width: lexiconScroll.availableWidth
            // 窗口够高就填满；太矮才出现整页滚动
            height: Math.max(lexiconScroll.height, 520)
            spacing: 16

            Item {
                objectName: "lexiconPageHeader"
                Layout.fillWidth: true
                Layout.preferredHeight: 60

                RowLayout {
                    anchors.left: parent.left
                    anchors.right: parent.right
                    height: 40
                    spacing: 12

                    Label {
                        objectName: "lexiconPageTitle"
                        text: qsTr("热词")
                        font.family: tokens.fontFamilyDisplay
                        font.pixelSize: tokens.fontSizePageTitle
                        font.weight: Font.DemiBold
                        color: tokens.textPrimary
                        Layout.alignment: Qt.AlignBaseline
                    }
                    Label {
                        objectName: "lexiconPageSubtitle"
                        text: qsTr("按场景纠正听错的词")
                        font.family: tokens.fontFamily
                        font.pixelSize: tokens.fontSizeBody
                        color: tokens.textTertiary
                        Layout.alignment: Qt.AlignBaseline
                        Layout.fillWidth: true
                    }
                }
            }

            // ── 设置：一条，三列等宽 ──
            Panel {
                objectName: "lexiconSettingsSection"
                Layout.fillWidth: true
                Layout.fillHeight: false
                fill: false
                titleText: qsTr("设置")

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 16

                    SettingColumn {
                        titleText: qsTr("按说话内容自动纠错")
                        descriptionText: qsTr("场景里的词说得越多，该场景的纠错越先启用；停下来一段时间会自动冷却")
                        switchData: CompactSwitch {
                            objectName: "lexiconEnabledSwitch"
                            tokens: root.tokens
                            checked: CapsWriterController.lexicon.enabled
                            onToggled: CapsWriterController.lexicon.setEnabled(checked)
                        }
                    }
                    Rectangle { Layout.fillHeight: true; Layout.preferredWidth: 1; color: root.tokens.line }
                    SettingColumn {
                        titleText: qsTr("短词弹出候选菜单")
                        descriptionText: qsTr("拿不准的短词在光标处列出相近的词：按数字键或点一下即可替换，并记住你的选择")
                        switchData: CompactSwitch {
                            objectName: "lexiconPopupSwitch"
                            tokens: root.tokens
                            enabled: CapsWriterController.lexicon.enabled
                            checked: CapsWriterController.lexicon.candidatePopup
                            onToggled: CapsWriterController.lexicon.setCandidatePopup(checked)
                        }
                    }
                    Rectangle { Layout.fillHeight: true; Layout.preferredWidth: 1; color: root.tokens.line }
                    SettingColumn {
                        titleText: qsTr("记录最近识别")
                        descriptionText: qsTr("只保存在本机，最多 300 条、7 天；用来统计热度，也是「最近识别」的来源")
                        switchData: CompactSwitch {
                            objectName: "lexiconRecordSwitch"
                            tokens: root.tokens
                            checked: CapsWriterController.lexicon.recordHistory
                            onToggled: CapsWriterController.lexicon.setRecordHistory(checked)
                        }
                        // 清空按钮放在说明文字下面，不挤开右边的开关
                        belowData: CompactButton {
                            objectName: "lexiconClearHistoryButton"
                            tokens: root.tokens
                            text: qsTr("清空记录")
                            onClicked: CapsWriterController.lexicon.clearHistory()
                        }
                    }
                }
            }

            // ── 下面：左右两个面板，高度一致、边界对齐 ──
            RowLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 16

                // 左：场景列表
                Panel {
                    objectName: "lexiconScenesSection"
                    Layout.fillWidth: true
                    Layout.preferredWidth: 5
                    Layout.fillHeight: true
                    titleText: qsTr("场景")

                    // 表头
                    RowLayout {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 30
                        spacing: 12
                        HeaderCell { text: qsTr("场景"); Layout.fillWidth: true }
                        HeaderCell { text: qsTr("模式"); Layout.preferredWidth: root.sceneModeWidth }
                        Item { Layout.preferredWidth: root.sceneActionWidth }
                    }
                    Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: root.tokens.line }

                    // 场景行：多了就在这里滚动
                    ListView {
                        id: sceneList
                        objectName: "lexiconSceneList"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        model: root.scenes
                        boundsBehavior: Flickable.StopAtBounds
                        ScrollBar.vertical: ScrollBar {}

                        delegate: Item {
                            id: sceneRow
                            required property var modelData
                            readonly property bool selected: root.selectedKey === modelData.id
                            objectName: "lexiconSceneRow"
                            width: ListView.view.width
                            height: 56

                            Rectangle {
                                anchors.fill: parent
                                anchors.topMargin: 2
                                anchors.bottomMargin: 2
                                radius: 8
                                color: sceneRow.selected ? root.tokens.accentSoft : "transparent"
                            }
                            MouseArea {
                                anchors.fill: parent
                                onClicked: root.selectedSceneId = sceneRow.modelData.id
                            }
                            RowLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 8
                                spacing: 12
                                // 状态指示灯：绿=已生效，灰=未生效
                                Rectangle {
                                    Layout.preferredWidth: 8
                                    Layout.preferredHeight: 8
                                    Layout.alignment: Qt.AlignVCenter
                                    radius: 4
                                    color: sceneRow.modelData.active ? root.tokens.successColor : root.tokens.textTertiary
                                }
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    Layout.minimumWidth: 60
                                    Layout.alignment: Qt.AlignVCenter
                                    spacing: 2
                                    BodyCell {
                                        text: sceneRow.modelData.name
                                        font.weight: Font.DemiBold
                                        Layout.fillWidth: true
                                    }
                                    BodyCell {
                                        text: (sceneRow.modelData.active ? qsTr("已生效") : qsTr("未生效"))
                                            + qsTr(" · 热度 %1").arg(sceneRow.modelData.heat.toFixed(1))
                                        font.pixelSize: 11
                                        color: sceneRow.modelData.active
                                            ? root.tokens.successText : root.tokens.textTertiary
                                        Layout.fillWidth: true
                                    }
                                }
                                SelectionComboBox {
                                    objectName: "lexiconModeCombo"
                                    tokens: root.tokens
                                    Layout.preferredWidth: root.sceneModeWidth
                                    Layout.alignment: Qt.AlignVCenter
                                    enabled: CapsWriterController.lexicon.enabled
                                    model: [qsTr("自动"), qsTr("始终开启"), qsTr("关闭")]
                                    currentIndex: sceneRow.modelData.mode === "on" ? 1
                                        : (sceneRow.modelData.mode === "off" ? 2 : 0)
                                    onActivated: (index) => CapsWriterController.lexicon.setSceneMode(
                                        sceneRow.modelData.id,
                                        index === 1 ? "on" : (index === 2 ? "off" : "auto"))
                                }
                                CompactButton {
                                    objectName: "lexiconRemoveSceneButton"
                                    tokens: root.tokens
                                    flat: true
                                    Layout.preferredWidth: root.sceneActionWidth
                                    Layout.alignment: Qt.AlignVCenter
                                    text: qsTr("删除")
                                    onClicked: CapsWriterController.lexicon.removeScene(sceneRow.modelData.id)
                                }
                            }
                            Rectangle {
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.bottom: parent.bottom
                                height: 1
                                color: root.tokens.line
                            }
                        }

                        // 没有场景时的提示
                        BodyCell {
                            anchors.centerIn: parent
                            width: parent.width - 32
                            visible: root.scenes.length === 0
                            text: qsTr("还没有场景。新建一个，或从「导入示例」开始。")
                            color: root.tokens.textTertiary
                            horizontalAlignment: Text.AlignHCenter
                            wrapMode: Text.Wrap
                        }
                    }

                    Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: root.tokens.line }
                    // 新建 + 导入示例：固定在列表下面
                    RowLayout {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 52
                        spacing: 8
                        CompactTextField {
                            id: newSceneField
                            objectName: "lexiconNewSceneField"
                            tokens: root.tokens
                            Layout.fillWidth: true
                            placeholderText: qsTr("新场景名称")
                        }
                        CompactButton {
                            objectName: "lexiconNewSceneButton"
                            tokens: root.tokens
                            text: qsTr("新建")
                            enabled: newSceneField.text.trim().length > 0
                            onClicked: {
                                if (CapsWriterController.lexicon.addScene(newSceneField.text))
                                    newSceneField.text = ""
                            }
                        }
                        CompactButton {
                            id: importExampleButton
                            objectName: "lexiconImportExampleButton"
                            tokens: root.tokens
                            flat: true
                            visible: CapsWriterController.lexicon.exampleScenes.length > 0
                            text: qsTr("导入示例")
                            trailingIcon: "chevron"
                            onClicked: examplesMenu.popup(importExampleButton, 0, importExampleButton.height)

                            Menu {
                                id: examplesMenu
                                Repeater {
                                    model: CapsWriterController.lexicon.exampleScenes
                                    delegate: MenuItem {
                                        required property var modelData
                                        text: qsTr("%1（%2 词）").arg(modelData.name).arg(modelData.count)
                                        onTriggered: CapsWriterController.lexicon.importExample(modelData.key)
                                    }
                                }
                            }
                        }
                    }
                }

                // 右：选中场景的别名 / 词表 + 最近识别
                Panel {
                    objectName: "lexiconDetailSection"
                    Layout.fillWidth: true
                    Layout.preferredWidth: 6
                    Layout.fillHeight: true
                    titleText: root.selectedScene
                        ? qsTr("「%1」").arg(root.selectedScene.name) : qsTr("场景详情")

                    // 页签
                    RowLayout {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 44
                        spacing: 6
                        CompactButton {
                            objectName: "lexiconTabAliases"
                            tokens: root.tokens
                            highlighted: root.detailTab === 0
                            flat: root.detailTab !== 0
                            text: qsTr("别名（%1）").arg(root.aliasRows.length)
                            onClicked: root.detailTab = 0
                        }
                        CompactButton {
                            objectName: "lexiconTabTerms"
                            tokens: root.tokens
                            highlighted: root.detailTab === 1
                            flat: root.detailTab !== 1
                            text: qsTr("词表（%1）").arg(root.termCount)
                            onClicked: root.detailTab = 1
                        }
                        CompactButton {
                            objectName: "lexiconTabChat"
                            tokens: root.tokens
                            highlighted: root.detailTab === 2
                            flat: root.detailTab !== 2
                            text: qsTr("最近识别")
                            onClicked: root.detailTab = 2
                        }
                        Item { Layout.fillWidth: true }
                    }
                    Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: root.tokens.line }

                    StackLayout {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        currentIndex: root.detailTab

                        // ── 页签 0：别名表格 ──
                        ColumnLayout {
                            spacing: 0

                            ListView {
                                id: aliasList
                                objectName: "lexiconAliasList"
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                clip: true
                                model: root.aliasRows
                                boundsBehavior: Flickable.StopAtBounds
                                headerPositioning: ListView.OverlayHeader
                                ScrollBar.vertical: ScrollBar {}

                                header: Rectangle {
                                    width: ListView.view.width
                                    height: 30
                                    z: 2
                                    color: root.tokens.surface
                                    RowLayout {
                                        anchors.fill: parent
                                        spacing: 12
                                        HeaderCell { text: qsTr("识别成"); Layout.preferredWidth: root.aliasWrongWidth }
                                        Item { Layout.preferredWidth: root.aliasArrowWidth }
                                        HeaderCell { text: qsTr("应为"); Layout.fillWidth: true }
                                        HeaderCell { text: qsTr("来源"); Layout.preferredWidth: root.aliasSourceWidth }
                                        Item { Layout.preferredWidth: root.aliasActionWidth }
                                    }
                                    Rectangle {
                                        anchors.left: parent.left
                                        anchors.right: parent.right
                                        anchors.bottom: parent.bottom
                                        height: 1
                                        color: root.tokens.line
                                    }
                                }

                                delegate: Item {
                                    id: aliasRow
                                    required property var modelData
                                    objectName: "lexiconAliasRow"
                                    width: ListView.view.width
                                    height: 36
                                    RowLayout {
                                        anchors.fill: parent
                                        spacing: 12
                                        BodyCell { text: aliasRow.modelData.wrong; Layout.preferredWidth: root.aliasWrongWidth }
                                        BodyCell {
                                            text: "→"
                                            color: root.tokens.textTertiary
                                            horizontalAlignment: Text.AlignHCenter
                                            Layout.preferredWidth: root.aliasArrowWidth
                                        }
                                        BodyCell { text: aliasRow.modelData.right; Layout.fillWidth: true }
                                        BodyCell {
                                            text: aliasRow.modelData.learned ? qsTr("自动学到") : qsTr("手动")
                                            color: root.tokens.textTertiary
                                            font.pixelSize: 12
                                            Layout.preferredWidth: root.aliasSourceWidth
                                        }
                                        CompactButton {
                                            tokens: root.tokens
                                            flat: true
                                            Layout.preferredWidth: root.aliasActionWidth
                                            text: aliasRow.modelData.learned ? qsTr("忘记") : qsTr("删除")
                                            onClicked: aliasRow.modelData.learned
                                                ? CapsWriterController.lexicon.forgetLearned(aliasRow.modelData.wrong)
                                                : CapsWriterController.lexicon.removeSceneAlias(
                                                    root.selectedScene.id, aliasRow.modelData.wrong)
                                        }
                                    }
                                    Rectangle {
                                        anchors.left: parent.left
                                        anchors.right: parent.right
                                        anchors.bottom: parent.bottom
                                        height: 1
                                        color: root.tokens.line
                                    }
                                }

                                BodyCell {
                                    anchors.centerIn: parent
                                    width: parent.width - 32
                                    visible: root.aliasRows.length === 0
                                    text: root.selectedScene
                                        ? qsTr("还没有别名。在下面添加，或在「最近识别」里点“加为别名”。")
                                        : qsTr("先在左边新建或选中一个场景。")
                                    color: root.tokens.textTertiary
                                    horizontalAlignment: Text.AlignHCenter
                                    wrapMode: Text.Wrap
                                }
                            }

                            Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: root.tokens.line }
                            // 添加一行：固定在表格下面，列与上面的表格对齐
                            RowLayout {
                                Layout.fillWidth: true
                                Layout.preferredHeight: 52
                                spacing: 12
                                CompactTextField {
                                    id: aliasWrongField
                                    objectName: "lexiconAliasWrongField"
                                    tokens: root.tokens
                                    enabled: root.selectedScene !== null
                                    Layout.preferredWidth: root.aliasWrongWidth
                                    placeholderText: qsTr("识别成")
                                }
                                BodyCell {
                                    text: "→"
                                    color: root.tokens.textTertiary
                                    horizontalAlignment: Text.AlignHCenter
                                    Layout.preferredWidth: root.aliasArrowWidth
                                }
                                CompactTextField {
                                    id: aliasRightField
                                    objectName: "lexiconAliasRightField"
                                    tokens: root.tokens
                                    enabled: root.selectedScene !== null
                                    Layout.fillWidth: true
                                    placeholderText: qsTr("应为")
                                }
                                CompactButton {
                                    objectName: "lexiconAddAliasButton"
                                    tokens: root.tokens
                                    Layout.preferredWidth: root.aliasSourceWidth + root.aliasActionWidth + 12
                                    text: qsTr("添加别名")
                                    enabled: root.selectedScene !== null
                                        && aliasWrongField.text.trim().length > 0
                                        && aliasRightField.text.trim().length > 0
                                    onClicked: {
                                        if (CapsWriterController.lexicon.addSceneAlias(
                                                root.selectedScene.id, aliasWrongField.text, aliasRightField.text)) {
                                            aliasWrongField.text = ""
                                            aliasRightField.text = ""
                                        }
                                    }
                                }
                            }
                        }

                        // ── 页签 1：词表（整块文字，逗号或换行分隔） ──
                        ColumnLayout {
                            spacing: 8

                            BodyCell {
                                Layout.fillWidth: true
                                Layout.topMargin: 10
                                text: qsTr("正确的词，用逗号或换行分隔；说到它们会给这个场景加热。改完点别处即保存。")
                                color: root.tokens.textTertiary
                                font.pixelSize: 12
                                wrapMode: Text.Wrap
                                elide: Text.ElideNone
                            }
                            ScrollView {
                                id: termsScroll
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                Layout.bottomMargin: 4
                                clip: true
                                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

                                TextArea {
                                    id: termsArea
                                    objectName: "lexiconTermsField"
                                    enabled: root.selectedScene !== null
                                    wrapMode: TextArea.Wrap
                                    selectByMouse: true
                                    font.family: root.tokens.fontFamily
                                    font.pixelSize: 13
                                    color: root.tokens.textPrimary
                                    placeholderText: qsTr("例如：近景，远景，特写")
                                    leftPadding: 12
                                    rightPadding: 12
                                    topPadding: 10
                                    bottomPadding: 10
                                    background: Rectangle {
                                        radius: 8
                                        color: root.tokens.surfaceMuted
                                    }
                                    function load() {
                                        text = root.selectedScene ? root.selectedScene.termsText : ""
                                    }
                                    Component.onCompleted: load()
                                    onEditingFinished: if (root.selectedScene)
                                        CapsWriterController.lexicon.setSceneTerms(root.selectedScene.id, text)
                                    Connections {
                                        target: root
                                        function onSelectedKeyChanged() { termsArea.load() }
                                    }
                                    Connections {
                                        target: CapsWriterController.lexicon
                                        function onChanged() {
                                            if (!termsArea.activeFocus)
                                                termsArea.load()
                                        }
                                    }
                                }
                            }
                        }

                        // ── 页签 2：最近识别（聊天样式） ──
                        ListView {
                            id: chatList
                            objectName: "lexiconChatList"
                            clip: true
                            spacing: 6
                            model: root.chatMessages
                            boundsBehavior: Flickable.StopAtBounds
                            topMargin: 8
                            bottomMargin: 8
                            ScrollBar.vertical: ScrollBar {}
                            onCountChanged: Qt.callLater(positionViewAtEnd)
                            onVisibleChanged: if (visible) Qt.callLater(positionViewAtEnd)

                            delegate: Item {
                                id: chatRow
                                required property var modelData
                                objectName: "lexiconChatRow"
                                width: ListView.view.width
                                height: bubble.height + 2

                                TextMetrics {
                                    id: textMetrics
                                    font.family: root.tokens.fontFamily
                                    font.pixelSize: 14
                                    text: chatRow.modelData.text
                                }
                                TextMetrics {
                                    id: metaMetrics
                                    font.family: root.tokens.fontFamily
                                    font.pixelSize: 11
                                    text: chatRow.modelData.time
                                        + (chatRow.modelData.corrected
                                            ? qsTr(" · 已纠正，原文：%1").arg(chatRow.modelData.raw) : "")
                                }

                                // 「加为别名」：听错的话点一下，填进别名表格的「识别成」
                                CompactButton {
                                    visible: !chatRow.modelData.corrected && root.selectedScene !== null
                                        && chatRow.width - bubble.width > 110
                                    anchors.verticalCenter: bubble.verticalCenter
                                    anchors.right: bubble.left
                                    anchors.rightMargin: 4
                                    tokens: root.tokens
                                    flat: true
                                    text: qsTr("加为别名")
                                    onClicked: root.openAliasDraft(chatRow.modelData.text)
                                }

                                Rectangle {
                                    id: bubble
                                    readonly property real innerWidth: Math.max(1, Math.min(
                                        Math.max(textMetrics.advanceWidth, metaMetrics.advanceWidth) + 2,
                                        chatRow.width * 0.72 - 28))
                                    anchors.right: parent.right
                                    anchors.rightMargin: 8
                                    anchors.top: parent.top
                                    width: innerWidth + 28
                                    height: bubbleText.implicitHeight + bubbleMeta.implicitHeight + 22
                                    radius: 14
                                    topRightRadius: 4
                                    color: root.tokens.accentSoft

                                    Label {
                                        id: bubbleText
                                        x: 14
                                        y: 10
                                        width: bubble.innerWidth
                                        text: chatRow.modelData.text
                                        wrapMode: Text.Wrap
                                        font.family: root.tokens.fontFamily
                                        font.pixelSize: 14
                                        color: root.tokens.textPrimary
                                    }
                                    Label {
                                        id: bubbleMeta
                                        x: 14
                                        y: 12 + bubbleText.implicitHeight
                                        width: bubble.innerWidth
                                        text: metaMetrics.text
                                        wrapMode: Text.Wrap
                                        font.family: root.tokens.fontFamily
                                        font.pixelSize: 11
                                        color: root.tokens.textTertiary
                                    }
                                }
                            }

                            BodyCell {
                                anchors.centerIn: parent
                                width: parent.width - 32
                                visible: root.chatMessages.length === 0
                                text: CapsWriterController.lexicon.recordHistory
                                    ? qsTr("还没有识别记录。按住遥控器话筒键说几句试试。")
                                    : qsTr("已关闭记录。")
                                color: root.tokens.textTertiary
                                horizontalAlignment: Text.AlignHCenter
                                wrapMode: Text.Wrap
                            }
                        }
                    }
                }
            }
        }
    }
}
