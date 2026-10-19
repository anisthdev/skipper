local mp = require("mp")
local options = require("mp.options")
local utils = require("mp.utils")

local opts = {
	command = "skipper",
	min_segment_ms = 1000,
	chapter_match_tolerance_ms = 5000,
}

options.read_options(opts, "skipper")

local request_id = 0

local function expand_path(path)
	if not path then
		return nil
	end

	if path:sub(1, 2) == "~/" then
		local home = os.getenv("HOME")
		if home then
			return home .. path:sub(2)
		end
	end

	return path
end

local function is_remote_path(path)
	return path and path:match("^%a[%w+.-]*://") ~= nil
end

local function absolute_media_path()
	local path = mp.get_property("path")
	if not path or path == "" or is_remote_path(path) then
		return nil
	end

	if path:sub(1, 1) == "/" then
		return path
	end

	local working_directory = mp.get_property("working-directory")
	if working_directory and working_directory ~= "" then
		return utils.join_path(working_directory, path)
	end

	return path
end

local function ms_to_seconds(value)
	if type(value) ~= "number" then
		return nil
	end

	return value / 1000
end

local function get_existing_chapters()
	local chapters = mp.get_property_native("chapter-list", {})
	if type(chapters) ~= "table" then
		return {}
	end

	return chapters
end

local function uosc_range_title(segment_type)
	if segment_type == "op" or segment_type == "mixed-op" then
		return "OP"
	elseif segment_type == "ed" or segment_type == "mixed-ed" then
		return "ED"
	elseif segment_type == "intro" then
		return "Opening"
	elseif segment_type == "credits" or segment_type == "ending" then
		return "Ending"
	elseif segment_type == "outro" then
		return "Outro"
	elseif segment_type == "recap" then
		return "Recap"
	elseif segment_type == "preview" then
		return "Preview"
	end

	return nil
end

local function chapter_title(segment, marker)
	local segment_type = segment.type or "segment"
	local title = uosc_range_title(segment_type)
	if marker == "start" then
		return title
	end
	return ""
end

local function build_chapters(segments)
	local chapters = {}

	for _, segment in ipairs(segments or {}) do
		local start_ms = segment.start_ms
		local end_ms = segment.end_ms

		if type(end_ms) ~= "number" or end_ms - start_ms >= opts.min_segment_ms then
			table.insert(chapters, {
				time = ms_to_seconds(start_ms),
				title = chapter_title(segment, "start"),
			})
			if type(end_ms) == "number" then
				table.insert(chapters, {
					time = ms_to_seconds(end_ms),
					title = chapter_title(segment, "end"),
				})
			end
		end
	end

	table.sort(chapters, function(a, b)
		return a.time < b.time
	end)

	return chapters
end

local function nearest_chapter(existing_chapters, time)
	local best_chapter = nil
	local best_delta = nil

	for _, chapter in ipairs(existing_chapters or {}) do
		if type(chapter.time) == "number" then
			local delta = math.abs(chapter.time - time)
			if best_delta == nil or delta < best_delta then
				best_chapter = chapter
				best_delta = delta
			end
		end
	end

	return best_chapter, best_delta
end

local function is_covered_by_existing(existing_chapters, time, tolerance_ms)
	local _, delta = nearest_chapter(existing_chapters, time)
	if delta == nil then
		return false
	end

	return delta <= (tolerance_ms / 1000)
end

local function merge_chapters(existing_chapters, generated_chapters)
	local merged = {}

	for _, chapter in ipairs(existing_chapters or {}) do
		table.insert(merged, chapter)
	end

	for _, chapter in ipairs(generated_chapters or {}) do
		table.insert(merged, chapter)
	end

	table.sort(merged, function(a, b)
		return a.time < b.time
	end)

	local deduped = {}
	local last_time = nil
	for _, chapter in ipairs(merged) do
		if type(chapter.time) == "number" and (last_time == nil or math.abs(chapter.time - last_time) > 0.001) then
			table.insert(deduped, chapter)
			last_time = chapter.time
		end
	end

	return deduped
end

local function select_generated_chapters(existing_chapters, generated_chapters)
	local selected = {}

	for _, chapter in ipairs(generated_chapters or {}) do
		local tolerance_ms = opts.chapter_match_tolerance_ms

		if not is_covered_by_existing(existing_chapters, chapter.time, tolerance_ms) then
			table.insert(selected, chapter)
		end
	end

	return selected
end

local function apply_chapters(payload)
	if type(payload) ~= "table" then
		mp.msg.warn("helper returned non-object JSON")
		return
	end

	if payload.ok ~= true then
		mp.msg.verbose("helper returned no chapters: " .. tostring(payload.error or payload.status))
		return
	end

	local chapters = build_chapters(payload.segments)
	if #chapters == 0 then
		mp.msg.verbose("helper returned no segments")
		return
	end

	local existing_chapters = get_existing_chapters()
	local additions = select_generated_chapters(existing_chapters, chapters)

	if #additions == 0 then
		mp.msg.verbose("existing chapters already cover skipper markers; leaving them unchanged")
		return
	end

	local merged = merge_chapters(existing_chapters, additions)
	mp.set_property_native("chapter-list", merged)
	mp.msg.info(
		string.format(
			"added %d skipper chapters from %s%s",
			#additions,
			payload.provider or "provider",
			#existing_chapters > 0 and " (reused nearby existing chapters)" or ""
		)
	)
end

local function run_helper()
	local media_path = absolute_media_path()
	if not media_path then
		mp.msg.verbose("skipper skipped non-local or missing path")
		return
	end

	local args = { expand_path(opts.command) }
	local duration = mp.get_property_number("duration")
	if duration and duration > 0 then
		table.insert(args, "--duration-ms")
		table.insert(args, tostring(math.floor(duration * 1000 + 0.5)))
	end
	table.insert(args, media_path)

	request_id = request_id + 1
	local this_request = request_id

	mp.command_native_async({
		name = "subprocess",
		playback_only = false,
		capture_stdout = true,
		capture_stderr = true,
		args = args,
	}, function(success, result, error_message)
		if this_request ~= request_id then
			return
		end

		if not success or not result then
			mp.msg.warn("helper subprocess failed: " .. tostring(error_message))
			return
		end

		if result.status ~= 0 then
			mp.msg.verbose("helper exited " .. tostring(result.status) .. ": " .. tostring(result.stderr))
		end

		local stdout = result.stdout or ""
		mp.msg.verbose("helper stdout: " .. stdout)

		local payload, parse_error = utils.parse_json(stdout)
		if not payload then
			mp.msg.warn("failed to parse helper JSON: " .. tostring(parse_error))
			return
		end

		apply_chapters(payload)
	end)
end

mp.register_event("file-loaded", run_helper)
