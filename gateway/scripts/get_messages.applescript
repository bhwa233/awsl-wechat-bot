-- WeChat 消息提取器 - 通过 Accessibility API
-- 用法: osascript get_messages.applescript [窗口索引]
-- 窗口索引默认为 1

on detectWeChatProcess()
	set processNames to {"WeChat", "微信"}
	repeat with procName in processNames
		tell application "System Events"
			if exists (process procName) then
				return procName
			end if
		end tell
	end repeat
	return missing value
end detectWeChatProcess

-- 查找消息列表元素
on findMessagesList(elem, depth, maxDepth)
	if depth > maxDepth then
		return missing value
	end if

	try
		tell application "System Events"
			set elemRole to role of elem
			if elemRole is "AXList" then
				try
					set elemTitle to title of elem
					if elemTitle is "Messages" then
						return elem
					end if
				end try
			end if

			try
				set children to UI elements of elem
				repeat with child in children
					set foundList to my findMessagesList(child, depth + 1, maxDepth)
					if foundList is not missing value then
						return foundList
					end if
				end repeat
			end try
		end tell
	end try

	return missing value
end findMessagesList

-- 从消息列表中提取所有消息
on extractMessages(messagesList)
	set messages to {}

	try
		tell application "System Events"
			set messageElements to UI elements of messagesList

			repeat with msgElem in messageElements
				try
					set msgTitle to title of msgElem
					if msgTitle is not missing value and msgTitle is not "" then
						set end of messages to msgTitle
					end if
				end try
			end repeat
		end tell
	end try

	return messages
end extractMessages

-- 主程序，接受窗口索引参数
on run argv
	set windowIndex to 1
	if (count of argv) > 0 then
		try
			set windowIndex to (item 1 of argv) as integer
		end try
	end if

	set wechatProcess to detectWeChatProcess()
	if wechatProcess is missing value then
		return "ERROR: 微信未运行"
	end if

	try
		tell application "System Events"
			tell process wechatProcess
				if windowIndex > (count of windows) then
					return "ERROR: 窗口索引超出范围"
				end if

				set targetWindow to window windowIndex
				perform action "AXRaise" of targetWindow

				set messagesList to my findMessagesList(targetWindow, 0, 10)

				if messagesList is not missing value then
					set messages to my extractMessages(messagesList)

					if (count of messages) > 0 then
						set AppleScript's text item delimiters to "|||"
						return "SUCCESS:" & (messages as text)
					else
						return "SUCCESS:NO_MESSAGES"
					end if
				else
					return "ERROR: 未找到消息列表"
				end if
			end tell
		end tell
	on error errMsg
		return "ERROR: " & errMsg
	end try
end run
