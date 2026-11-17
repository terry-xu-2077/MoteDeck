import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// 设置行（稿里的 .row）：
//   .h44 → 行高 48，标题与说明横排、基线对齐、间距 12（标题可固定 168 宽）
//   .h60 → 行高 64，标题 13/18 在上，说明 12/16 在下，间距 2
//   右侧 .act 间距 4；行尾 1px line 分隔线。
Item {
    id: root

    property var tokens
    property string titleText: ""
    property string descriptionText: ""
    property bool showDivider: true
    // 标题固定宽度（稿里的 168px），0 表示标题自适应
    property int titleWidth: 0
    property bool inlineLabel: false
    property int rowHeight: 48
    // 说明文字最多显示几行（默认 1 行，超出省略）；>1 时自动换行
    property int descriptionLines: 1
    // 右侧开关/按钮与标题那一行垂直对齐（而不是在整块文字的正中间）；多行说明时更好看
    property bool actionsAlignTitle: false
    readonly property int topPadding: 10
    property string titleObjectName: ""
    // 标题右侧紧跟的内容（稿里语音页 .lbl 之后的 pill）
    property alias trailingData: trailingHolder.data
    default property alias actions: actionHolder.data
    // 说明文字下面再放一行内容（比如一个次要按钮）
    property alias belowData: belowHolder.data

    readonly property bool inlineMode: titleWidth > 0 || inlineLabel

    implicitHeight: rowHeight

    RowLayout {
        anchors.fill: parent
        spacing: 12

        RowLayout {
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            spacing: 12
            visible: root.inlineMode

            Label {
                id: titleLabelCompact
                objectName: root.inlineMode ? root.titleObjectName : ""
                text: root.titleText
                font.family: root.tokens.fontFamily
                font.pixelSize: 13
                font.weight: Font.DemiBold
                lineHeight: 18
                lineHeightMode: Text.FixedHeight
                color: root.tokens.textPrimary
                Layout.alignment: Qt.AlignBaseline
                Layout.preferredWidth: root.titleWidth > 0 ? root.titleWidth
                    : titleLabelCompact.implicitWidth
                elide: Text.ElideRight
            }
            Label {
                text: root.descriptionText
                font.family: root.tokens.fontFamily
                font.pixelSize: 12
                lineHeight: 16
                lineHeightMode: Text.FixedHeight
                color: root.tokens.textTertiary
                Layout.alignment: Qt.AlignBaseline
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                elide: Text.ElideRight
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            Layout.alignment: root.actionsAlignTitle ? Qt.AlignTop : Qt.AlignVCenter
            Layout.topMargin: root.actionsAlignTitle ? root.topPadding : 0
            spacing: 2
            visible: !root.inlineMode

            Label {
                id: titleLabel
                objectName: !root.inlineMode ? root.titleObjectName : ""
                text: root.titleText
                font.family: root.tokens.fontFamily
                font.pixelSize: 13
                font.weight: Font.DemiBold
                lineHeight: 18
                lineHeightMode: Text.FixedHeight
                color: root.tokens.textPrimary
                elide: Text.ElideRight
                Layout.fillWidth: true
            }
            Label {
                text: root.descriptionText
                font.family: root.tokens.fontFamily
                font.pixelSize: 12
                lineHeight: 16
                lineHeightMode: Text.FixedHeight
                color: root.tokens.textTertiary
                wrapMode: root.descriptionLines > 1 ? Text.Wrap : Text.NoWrap
                maximumLineCount: Math.max(1, root.descriptionLines)
                elide: Text.ElideRight
                Layout.fillWidth: true
                visible: text.length > 0
            }
            RowLayout {
                id: belowHolder
                Layout.topMargin: 4
                spacing: 4
                visible: children.length > 0
            }
        }

        RowLayout {
            id: trailingHolder
            spacing: 4
            visible: children.length > 0
            Layout.alignment: Qt.AlignVCenter
        }

        RowLayout {
            id: actionHolder
            spacing: 4
            Layout.alignment: root.actionsAlignTitle ? Qt.AlignTop : Qt.AlignVCenter
            // 标题那一行的中线：上边距 + 行高的一半；控件按自己的高度居中到这条线上
            Layout.topMargin: root.actionsAlignTitle
                ? Math.max(0, root.topPadding + 9 - actionHolder.implicitHeight / 2) : 0
        }
    }

    Rectangle {
        visible: root.showDivider
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: root.tokens.hairlineWidth
        color: root.tokens.line
    }
}
